import asyncio

from app.api import langgraph as langgraph_api
from fastapi import HTTPException
from langchain_core.messages import AIMessageChunk


async def _collect_response_body(response) -> str:
    parts: list[str] = []
    async for chunk in response.body_iterator:
        parts.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
    return "".join(parts)


def test_langgraph_query_builds_streaming_response(monkeypatch) -> None:
    async def fake_graph_stream():
        yield AIMessageChunk(content="推荐这款"), {}
        yield AIMessageChunk(content="忽略", additional_kwargs={"tool_calls": [{"id": "1"}]}), {}
        yield AIMessageChunk(content="忽略"), {"tags": ["research_plan"]}
        yield AIMessageChunk(content=""), {}

    async def scenario() -> None:
        monkeypatch.setattr(langgraph_api.uuid, "uuid4", lambda: "thread-1")

        def fake_astream(*, input, stream_mode, config):
            assert input.messages[0].content == "空调推荐"
            assert stream_mode == "messages"
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


def test_langgraph_query_wraps_unexpected_setup_error(monkeypatch) -> None:
    def broken_astream(**_kwargs):
        raise RuntimeError("boom")

    async def scenario() -> None:
        monkeypatch.setattr(langgraph_api.graph, "astream", broken_astream)

        try:
            await langgraph_api.langgraph_query(
                query="空调推荐",
                user_id=3,
                conversation_id="thread-1",
            )
        except HTTPException as exc:
            assert exc.status_code == 500
            assert exc.detail == "Internal server error"
        else:
            raise AssertionError("expected HTTPException")

    asyncio.run(scenario())
