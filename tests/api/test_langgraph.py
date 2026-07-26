import asyncio

from app.api import langgraph as langgraph_api
from app.chat.application import agent_query_service


class FakeChunk:
    def __init__(self, content: str, additional_kwargs=None) -> None:
        self.content = content
        self.additional_kwargs = additional_kwargs or {}


async def _collect_response_body(response) -> str:
    parts: list[str] = []
    async for chunk in response.body_iterator:
        parts.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
    return "".join(parts)


def test_langgraph_query_builds_streaming_response(monkeypatch) -> None:
    async def fake_graph_stream():
        yield FakeChunk("推荐这款"), {}
        yield FakeChunk("忽略", {"tool_calls": [{"id": "1"}]}), {}
        yield FakeChunk("忽略", {}), {"tags": ["research_plan", 1]}
        yield FakeChunk("正常输出", ["bad"]), {}
        yield FakeChunk(""), {}

    async def scenario() -> None:
        monkeypatch.setattr(langgraph_api.uuid, "uuid4", lambda: "thread-1")

        def fake_stream_agent_query(*, query, user_id, thread_id):
            assert query == "空调推荐"
            assert user_id == 3
            assert thread_id == "thread-1"
            return fake_graph_stream()

        monkeypatch.setattr(
            langgraph_api,
            "stream_agent_query",
            fake_stream_agent_query,
        )
        # also keep service module consistent if re-imported
        monkeypatch.setattr(
            agent_query_service,
            "stream_agent_query",
            fake_stream_agent_query,
        )

        response = await langgraph_api.langgraph_query(
            query="空调推荐",
            user_id=3,
            conversation_id=None,
        )

        assert response.headers["X-Conversation-ID"] == "thread-1"
        assert await _collect_response_body(response) == (
            'data: "推荐这款"\n\n'
            'data: "正常输出"\n\n'
        )

    asyncio.run(scenario())


def test_langgraph_query_emits_error_event_on_mid_stream_failure(monkeypatch) -> None:
    """流中途异常必须转成 event: error 帧。

    回归背景：外层 try/except 只覆盖流创建；StreamingResponse 开始后
    generator 抛错，客户端只看到连接静默断掉，无法区分"生成完了"和"炸了"。
    """

    async def broken_graph_stream():
        yield FakeChunk("前半段"), {}
        raise RuntimeError("llm exploded")

    async def scenario() -> None:
        monkeypatch.setattr(
            langgraph_api,
            "stream_agent_query",
            lambda **_kwargs: broken_graph_stream(),
        )

        response = await langgraph_api.langgraph_query(
            query="任意问题",
            user_id=3,
            conversation_id="thread-x",
        )
        body = await _collect_response_body(response)

        # 已生成内容照常发出；随后是命名 error 事件而不是静默断流
        assert 'data: "前半段"\n\n' in body
        assert "event: error\n" in body
        assert "生成过程中出现异常" in body

    asyncio.run(scenario())


def test_format_sse_error_frame_shape() -> None:
    frame = langgraph_api.format_sse_error("出错了")

    assert frame == 'event: error\ndata: "出错了"\n\n'
