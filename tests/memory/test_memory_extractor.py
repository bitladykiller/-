import asyncio

from app.knowledge.domain.schemas import SessionSummary
import app.knowledge.infrastructure.orchestration.memory_extractor as memory_extractor
from app.knowledge.infrastructure.orchestration.memory_extractor import (
    MemoryExtractor,
    build_semantic_memories,
    extract_response_text,
    extract_summary_text,
    parse_llm_response,
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


def test_extract_response_text_supports_string_and_list_content() -> None:
    assert extract_response_text("plain text") == "plain text"
    assert extract_response_text(
        FakeResponseObject(
            [
                {"text": "第一段"},
                "第二段",
                FakeTextPart("第三段"),
                {"other": "ignored"},
            ]
        )
    ) == "第一段\n第二段\n第三段"


def test_extract_first_json_object_ignores_prefix_and_suffix() -> None:
    payload = memory_extractor._extract_first_json_object(
        '前置说明 {"semantic":[{"content":"a { brace } inside string"}],"profile":{}} 后置说明'
    )

    assert payload == '{"semantic":[{"content":"a { brace } inside string"}],"profile":{}}'


def test_parse_llm_response_returns_empty_for_invalid_payload() -> None:
    assert parse_llm_response("没有 JSON") == {}
    assert parse_llm_response('["not-a-dict"]') == {}
    assert parse_llm_response('前置 {"semantic": [}') == {}


def test_build_semantic_memories_masks_and_filters_items() -> None:
    extractor = MemoryExtractor(FakeLLMClient("{}"))
    parsed = {
        "semantic": [
            {
                "memory_type": "issue_history",
                "content": "用户手机号13812345678反馈门铃总是掉线",
                "reason": "含长期问题",
            },
            {
                "memory_type": "solution_note",
                "content": "谢谢",
            },
            {
                "memory_type": "invalid_type",
                "content": "这条类型无效但内容很长很长",
            },
        ]
    }

    semantic = build_semantic_memories(
        parsed,
        sensitive_patterns=extractor.sensitive_patterns,
    )

    assert len(semantic) == 1
    assert semantic[0].memory_type == "issue_history"
    assert semantic[0].content == "用户手机号138****5678反馈门铃总是掉线"
    assert semantic[0].reason == "含长期问题"


def test_build_semantic_memories_masks_multiple_sensitive_patterns() -> None:
    extractor = MemoryExtractor(FakeLLMClient("{}"))
    parsed = {
        "semantic": [
            {
                "memory_type": "issue_history",
                "content": (
                    "用户手机号13812345678，身份证100000000000000000，"
                    "银行卡6222021234567890，邮箱abc@example.com"
                ),
            }
        ]
    }

    semantic = build_semantic_memories(
        parsed,
        sensitive_patterns=extractor.sensitive_patterns,
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


def test_extract_summary_text_supports_model_and_string() -> None:
    assert extract_summary_text(SessionSummary(content=" 历史摘要 ")) == "历史摘要"
    assert extract_summary_text("  直接摘要 ") == "直接摘要"
    assert extract_summary_text(None) == ""
