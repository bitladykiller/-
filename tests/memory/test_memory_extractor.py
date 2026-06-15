import asyncio

from app.knowledge.domain.schemas import SessionSummary
from app.knowledge.infrastructure.orchestration.memory_extractor import (
    MemoryExtractor,
)


class FakeLLMClient:
    def __init__(self, response) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str):
        self.prompts.append(prompt)
        return self.response


class FakeResponseObject:
    def __init__(self, content) -> None:
        self.content = content


class FakeTextPart:
    def __init__(self, text: str) -> None:
        self.text = text


def _run(awaitable):
    return asyncio.run(awaitable)


def test_memory_extractor_extract_supports_list_content_response() -> None:
    extractor = MemoryExtractor(
        FakeLLMClient(
            FakeResponseObject(
                [
                    {"text": "前置说明"},
                    '{"semantic":[{"memory_type":"issue_history","content":"用户手机号13812345678反馈门铃总是掉线","reason":"含长期问题"}],',
                    FakeTextPart('"profile":{"preferred_brand":"  apple "}}'),
                    {"text": "}"},
                    {"other": "ignored"},
                ]
            )
        )
    )

    semantic, profile = _run(
        extractor.extract(
            "门铃总是掉线",
            "我来帮你排查",
            "  直接摘要 ",
        )
    )

    assert "当前会话摘要：直接摘要" in extractor.llm_client.prompts[0]
    assert [item.model_dump() for item in semantic] == [
        {
            "memory_type": "issue_history",
            "content": "用户手机号138****5678反馈门铃总是掉线",
            "reason": "含长期问题",
        }
    ]
    assert profile == {"preferred_brand": "apple"}


def test_memory_extractor_extract_returns_empty_for_invalid_payload() -> None:
    extractor = MemoryExtractor(FakeLLMClient("没有 JSON"))

    semantic, profile = _run(
        extractor.extract(
            "门铃还是断线",
            "我来继续帮你排查",
        )
    )

    assert semantic == []
    assert profile == {}


def test_memory_extractor_extract_masks_and_filters_semantic_items() -> None:
    extractor = MemoryExtractor(
        FakeLLMClient(
            FakeResponseObject(
                '{"semantic":['
                '{"memory_type":"issue_history","content":"用户手机号13812345678，身份证100000000000000000，银行卡6222021234567890，邮箱abc@example.com"},'
                '{"memory_type":"solution_note","content":"谢谢"},'
                '{"memory_type":"invalid_type","content":"这条类型无效但内容很长很长"}'
                '],"profile":{}}'
            )
        )
    )

    semantic, profile = _run(
        extractor.extract(
            "我留下了联系方式",
            "好的",
        )
    )

    assert [item.model_dump() for item in semantic] == [
        {
            "memory_type": "issue_history",
            "content": (
                "用户手机号138****5678，身份证1000**********0000，"
                "银行卡6222 **** **** 7890，邮箱a***@example.com"
            ),
            "reason": None,
        }
    ]
    assert profile == {}


def test_memory_extractor_extract_returns_semantic_and_normalized_profile() -> None:
    llm_client = FakeLLMClient(
        FakeResponseObject(
            '说明：{"semantic":[{"memory_type":"solution_note","content":"联系 aaa@example.com 后问题已解决","reason":"已验证"}],"profile":{"preferred_brand":"  apple ","tags":["极客","极客",""],"facts":[{"key":" pet ","value":" cat "} ]}}'
        )
    )
    extractor = MemoryExtractor(llm_client)
    session_summary = SessionSummary(content="用户之前在比较门铃品牌")

    semantic, profile = _run(
        extractor.extract(
            "我已经按说明联系邮箱处理了",
            "好的，问题已经记录并解决",
            session_summary,
        )
    )

    assert "当前会话摘要：用户之前在比较门铃品牌" in llm_client.prompts[0]
    assert [item.model_dump() for item in semantic] == [
        {
            "memory_type": "solution_note",
            "content": "联系 aaa***@example.com 后问题已解决",
            "reason": "已验证",
        }
    ]
    assert profile == {
        "preferred_brand": "apple",
        "tags": ["极客"],
        "facts": [{"key": "pet", "value": "cat"}],
    }


def test_memory_extractor_extract_returns_empty_when_llm_invoke_fails() -> None:
    class FailingLLMClient:
        async def ainvoke(self, prompt: str):
            raise RuntimeError("boom")

    extractor = MemoryExtractor(FailingLLMClient())

    semantic, profile = _run(
        extractor.extract(
            "门铃还是断线",
            "我来继续帮你排查",
        )
    )

    assert semantic == []
    assert profile == {}
