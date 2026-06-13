import asyncio

import app.api.langgraph_support as langgraph_support


class FakeChunk:
    def __init__(self, content, additional_kwargs=None) -> None:
        self.content = content
        self.additional_kwargs = additional_kwargs


async def _single_payload_stream(payload: str):
    yield payload


def _run(awaitable):
    return asyncio.run(awaitable)


def test_build_thread_config_and_input_state_are_stable() -> None:
    config = langgraph_support.build_thread_config("thread-1", 7)
    input_state = langgraph_support.build_input_state("你好")

    assert config == {
        "configurable": {
            "thread_id": "thread-1",
            "user_id": "7",
        }
    }
    assert len(input_state.messages) == 1
    assert input_state.messages[0].content == "你好"


def test_resolve_thread_id_reuses_existing_or_generates_new(monkeypatch) -> None:
    monkeypatch.setattr(langgraph_support, "new_uuid", lambda: "generated-thread")

    assert langgraph_support.resolve_thread_id("existing-thread") == "existing-thread"
    assert langgraph_support.resolve_thread_id(None) == "generated-thread"


def test_chunk_helpers_filter_tool_calls_and_research_plan() -> None:
    assert langgraph_support.chunk_tags({"tags": ["a", 1, "b"]}) == ["a", "b"]
    assert langgraph_support.chunk_tags({"tags": "bad"}) == []
    assert langgraph_support.coerce_additional_kwargs(
        FakeChunk("hi", additional_kwargs=["bad"])
    ) == {}

    assert langgraph_support.should_skip_chunk(
        content="hi",
        additional_kwargs={"tool_calls": [{"id": "1"}]},
        metadata={},
    ) is True
    assert langgraph_support.should_skip_chunk(
        content="hi",
        additional_kwargs={},
        metadata={"tags": ["research_plan"]},
    ) is True
    assert langgraph_support.should_skip_chunk(
        content="hi",
        additional_kwargs={},
        metadata={},
    ) is False


def test_serialize_stream_chunk_and_sse_payload_skip_invalid_chunks() -> None:
    normal_chunk = FakeChunk("推荐看看这款空调", additional_kwargs={})
    tool_chunk = FakeChunk("忽略", additional_kwargs={"tool_calls": [{"id": "1"}]})

    assert (
        langgraph_support.serialize_stream_chunk(normal_chunk, {})
        == '"推荐看看这款空调"'
    )
    assert langgraph_support.serialize_stream_chunk(tool_chunk, {}) is None
    assert langgraph_support.build_sse_payload('"ok"') == 'data: "ok"\n\n'


def test_build_streaming_response_sets_header_and_media_type() -> None:
    response = langgraph_support.build_streaming_response(
        _single_payload_stream('data: "ok"\n\n'),
        "thread-9",
    )

    assert response.media_type == "text/event-stream"
    assert response.headers["X-Conversation-ID"] == "thread-9"
