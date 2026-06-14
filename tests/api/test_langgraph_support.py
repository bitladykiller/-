import app.api.langgraph as langgraph_api


class FakeChunk:
    def __init__(self, content, additional_kwargs=None) -> None:
        self.content = content
        self.additional_kwargs = additional_kwargs


def test_serialize_stream_chunk_and_sse_payload_skip_invalid_chunks() -> None:
    normal_chunk = FakeChunk("推荐看看这款空调", additional_kwargs={})
    tool_chunk = FakeChunk("忽略", additional_kwargs={"tool_calls": [{"id": "1"}]})
    bad_kwargs_chunk = FakeChunk("正常输出", additional_kwargs=["bad"])
    research_plan_chunk = FakeChunk("忽略", additional_kwargs={})

    assert (
        langgraph_api.serialize_stream_chunk(normal_chunk, {})
        == '"推荐看看这款空调"'
    )
    assert langgraph_api.serialize_stream_chunk(tool_chunk, {}) is None
    assert (
        langgraph_api.serialize_stream_chunk(bad_kwargs_chunk, {})
        == '"正常输出"'
    )
    assert (
        langgraph_api.serialize_stream_chunk(
            research_plan_chunk,
            {"tags": ["research_plan", 1]},
        )
        is None
    )
    assert langgraph_api.build_sse_payload('"ok"') == 'data: "ok"\n\n'
