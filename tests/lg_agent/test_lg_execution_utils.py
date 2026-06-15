import asyncio

from app.chat.infrastructure.graph import execution_utils as lg_execution_utils


class FakeRetriever:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.queries: list[str] = []

    async def search(self, task: str) -> dict:
        self.queries.append(task)
        return self.result


class FakeChain:
    def __init__(self, result: object) -> None:
        self.result = result
        self.payloads: list[dict[str, str]] = []

    async def ainvoke(self, payload: dict[str, str]) -> object:
        self.payloads.append(payload)
        return self.result


class FakePrompt:
    def __init__(self, chain: FakeChain) -> None:
        self.chain = chain
        self.messages: list[tuple[str, str]] | None = None
        self.structured_model: object | None = None

    def __or__(self, structured_model: object) -> FakeChain:
        self.structured_model = structured_model
        return self.chain


class FakeModel:
    def __init__(self) -> None:
        self.schemas: list[type[object]] = []

    def with_structured_output(self, schema: type[object]) -> object:
        self.schemas.append(schema)
        return self


class FakeSchema:
    pass


def _run(awaitable):
    return asyncio.run(awaitable)


def test_search_retriever_placeholder_and_records_from_result() -> None:
    result = _run(lg_execution_utils.search_retriever(None, "查订单"))

    assert result == {
        "task": "查订单",
        "records": [],
        "errors": [],
        "steps": [],
    }
    assert lg_execution_utils.records_from_result(result) == []
    assert lg_execution_utils.records_from_result({"records": "bad"}) == []


def test_merge_retriever_records_preserves_order() -> None:
    merged = lg_execution_utils.merge_retriever_records(
        {"records": [{"source": "kg"}]},
        {"records": [{"source": "rag-1"}, {"source": "rag-2"}]},
    )

    assert merged == [
        {"source": "kg"},
        {"source": "rag-1"},
        {"source": "rag-2"},
    ]


def test_search_retriever_delegates_to_retriever() -> None:
    retriever = FakeRetriever({"records": [{"id": 1}]})

    result = _run(lg_execution_utils.search_retriever(retriever, "订单状态"))

    assert result == {"records": [{"id": 1}]}
    assert retriever.queries == ["订单状态"]


def test_summarize_and_build_response_uses_summary_result(monkeypatch) -> None:
    chain = FakeChain("推荐 A1")
    monkeypatch.setattr(lg_execution_utils, "_summarize_chain", chain)

    payload = _run(
        lg_execution_utils.summarize_and_build_response(
            "预算 3000",
            [{"product": "A1"}],
            progress_message="正在查询...",
            fallback="无结果",
        )
    )

    assert [message.content for message in payload["messages"]] == [
        "正在查询...",
        "推荐 A1",
    ]
    assert chain.payloads == [
        {"question": "预算 3000", "results": [[{"product": "A1"}]]}
    ]


def test_summarize_and_build_response_returns_fallback_for_empty_records(monkeypatch) -> None:
    monkeypatch.setattr(lg_execution_utils, "_summarize_chain", None)

    payload = _run(
        lg_execution_utils.summarize_and_build_response(
            "预算 3000",
            [],
            progress_message="正在查询...",
            fallback="无结果",
        )
    )

    assert [message.content for message in payload["messages"]] == [
        "正在查询...",
        "无结果",
    ]


def test_ainvoke_structured_question_output_builds_prompt_chain(monkeypatch) -> None:
    chain = FakeChain({"decision": "continue"})
    prompt = FakePrompt(chain)
    model = FakeModel()

    def fake_from_messages(messages: list[tuple[str, str]]) -> FakePrompt:
        prompt.messages = messages
        return prompt

    monkeypatch.setattr(
        lg_execution_utils.ChatPromptTemplate,
        "from_messages",
        fake_from_messages,
    )

    result = _run(
        lg_execution_utils.ainvoke_structured_question_output(
            system_prompt="系统",
            human_prompt="问题：{question}",
            model=model,
            output_schema=FakeSchema,
            question="订单什么时候到",
        )
    )

    assert result == {"decision": "continue"}
    assert prompt.messages == [
        ("system", "系统"),
        ("human", "问题：{question}"),
    ]
    assert model.schemas == [FakeSchema]
    assert chain.payloads == [{"question": "订单什么时候到"}]
