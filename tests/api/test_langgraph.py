import asyncio

from app.api import langgraph as langgraph_api


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

    async def scenario() -> None:
        monkeypatch.setattr(langgraph_api.uuid, "uuid4", lambda: "thread-1")

        def fake_astream(*, input, stream_mode, config):
            assert input.messages[0].content == "空调推荐"
            assert stream_mode == langgraph_api.STREAM_MODE_MESSAGES
            assert config == {
                "configurable": {
                    "thread_id": "thread-1",
                    "user_id": "3",
                }
            }
            return fake_graph_stream()

        monkeypatch.setattr(langgraph_api.graph, "astream", fake_astream)

        response = await langgraph_api.langgraph_query(
            query="空调推荐",
            user_id=3,
            conversation_id=None,
        )

        assert response.headers["X-Conversation-ID"] == "thread-1"
        assert await _collect_response_body(response) == 'data: "推荐这款"\n\n'

    asyncio.run(scenario())
