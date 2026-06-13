import asyncio

from app.memory.memory_middleware_support import (
    build_summary_compressor,
    build_turn_messages,
    coerce_llm_response_text,
    load_long_term_memory_state,
    load_short_term_memory_state,
    load_user_profile_state,
    save_profile_if_present,
    save_semantic_memories,
    save_short_term_turn,
    update_hit_long_term_memories,
)
from app.memory.schemas import (
    AgentMemoryState,
    LongTermMemory,
    MemoryExtractorResult,
    MemorySearchResult,
    MessageRecord,
    SessionMeta,
    SessionSummary,
)


class FakeRedisSTM:
    def __init__(self) -> None:
        self.summary = SessionSummary(
            content="历史摘要",
            compressed_at=10,
            compressed_round=2,
        )
        self.recent_messages = [
            MessageRecord(
                message_id="msg-1",
                role="user",
                content="旧问题",
                created_at=1,
                turn_index=1,
            )
        ]
        self.meta = SessionMeta(total_turns=3, last_updated_at=0, last_compressed_turn=1)
        self.appended_messages: list[MessageRecord] = []
        self.saved_meta: SessionMeta | None = None
        self.refresh_calls = 0

    async def get_summary(self, tenant_id: str, user_id: str, session_id: str) -> SessionSummary:
        return self.summary

    async def get_recent_messages(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> list[MessageRecord]:
        return self.recent_messages

    async def get_meta(self, tenant_id: str, user_id: str, session_id: str) -> SessionMeta:
        return self.meta

    async def append_message(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        message: MessageRecord,
    ) -> None:
        self.appended_messages.append(message)

    async def save_meta(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        meta: SessionMeta,
    ) -> None:
        self.saved_meta = meta.model_copy(deep=True)

    async def refresh_ttl(self, tenant_id: str, user_id: str, session_id: str) -> None:
        self.refresh_calls += 1


class FakeLongTermMemory:
    def __init__(self) -> None:
        self.hybrid_results: list[MemorySearchResult] = []
        self.hybrid_search_calls: list[tuple[str, str, str]] = []
        self.deduplicate_results: list[bool] = []
        self.deduplicate_calls: list[tuple[str, str, str, str]] = []
        self.saved_memories: list[tuple[str, str, str, str]] = []
        self.updated_ids: list[str] = []

    async def hybrid_search(
        self,
        tenant_id: str,
        user_id: str,
        user_input: str,
    ) -> list[MemorySearchResult]:
        self.hybrid_search_calls.append((tenant_id, user_id, user_input))
        return self.hybrid_results

    async def deduplicate_memory(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        content: str,
    ) -> bool:
        self.deduplicate_calls.append((tenant_id, user_id, memory_type, content))
        return self.deduplicate_results.pop(0)

    async def save_memory(
        self,
        tenant_id: str,
        user_id: str,
        memory_type: str,
        content: str,
    ) -> str:
        self.saved_memories.append((tenant_id, user_id, memory_type, content))
        return "mem-saved"

    async def update_memory_hit_info(self, memory: LongTermMemory) -> bool:
        if memory.memory_id == "mem-fail":
            raise RuntimeError("boom")
        self.updated_ids.append(memory.memory_id)
        return True


class FakeLLMClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str):
        self.prompts.append(prompt)
        return type("Response", (), {"content": "压缩后摘要"})()


def _run(awaitable):
    return asyncio.run(awaitable)


def test_build_turn_messages_and_response_coercion_are_stable() -> None:
    messages = build_turn_messages(
        user_message="用户问题",
        assistant_message="助手回答",
        created_at=123,
        turn_index=4,
    )

    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].message_id == "msg_u_123"
    assert messages[1].message_id == "msg_a_123"
    assert coerce_llm_response_text(type("Response", (), {"content": "abc"})()) == "abc"
    assert coerce_llm_response_text({"foo": "bar"}) == "{'foo': 'bar'}"


def test_load_state_helpers_populate_memory_layers() -> None:
    redis_stm = FakeRedisSTM()
    milvus_ltm = FakeLongTermMemory()
    milvus_ltm.hybrid_results = [
        MemorySearchResult(
            memory=LongTermMemory(
                memory_id="mem-1",
                tenant_id="tenant-1",
                user_id="42",
                memory_type="issue_history",
                content="之前问过空调维修",
            ),
            score=0.9,
        )
    ]
    memory_state = AgentMemoryState()
    profile_reader_calls: list[tuple[int, object | None]] = []

    async def fake_profile_reader(user_id: int, redis_client: object | None):
        profile_reader_calls.append((user_id, redis_client))
        return {"preferred_brand": "海尔"}

    _run(
        load_short_term_memory_state(
            memory_state,
            redis_stm=redis_stm,
            tenant_id="tenant-1",
            user_id="42",
            session_id="session-1",
        )
    )
    _run(
        load_user_profile_state(
            memory_state,
            profile_reader=fake_profile_reader,
            profile_cache="cache",
            user_id="42",
        )
    )
    _run(
        load_long_term_memory_state(
            memory_state,
            milvus_ltm=milvus_ltm,
            ltm_enabled=True,
            tenant_id="tenant-1",
            user_id="42",
            user_input="怎么修空调",
        )
    )

    assert memory_state.session_summary == redis_stm.summary
    assert memory_state.recent_messages == redis_stm.recent_messages
    assert memory_state.user_profile == {"preferred_brand": "海尔"}
    assert profile_reader_calls == [(42, "cache")]
    assert milvus_ltm.hybrid_search_calls == [("tenant-1", "42", "怎么修空调")]
    assert memory_state.long_term_memories[0].memory.memory_id == "mem-1"


def test_save_short_term_turn_updates_meta_and_refreshes_ttl() -> None:
    redis_stm = FakeRedisSTM()

    _run(
        save_short_term_turn(
            redis_stm=redis_stm,
            tenant_id="tenant-1",
            user_id="42",
            session_id="session-1",
            user_message="这轮用户消息",
            assistant_message="这轮助手回复",
            now_ts=200,
        )
    )

    assert [message.role for message in redis_stm.appended_messages] == ["user", "assistant"]
    assert redis_stm.saved_meta is not None
    assert redis_stm.saved_meta.total_turns == 4
    assert redis_stm.saved_meta.last_updated_at == 200
    assert redis_stm.refresh_calls == 1


def test_build_summary_compressor_uses_prompt_builder_and_response_content(monkeypatch) -> None:
    llm_client = FakeLLMClient()
    monkeypatch.setattr(
        "app.memory.memory_middleware_support.build_compression_prompt",
        lambda **kwargs: f"round={kwargs['compressed_round']} summary={kwargs['old_summary']}",
    )
    compressor = build_summary_compressor(
        llm_client=llm_client,
        compressed_round=3,
    )

    result = _run(compressor("旧摘要", []))

    assert result == "压缩后摘要"
    assert llm_client.prompts == ["round=3 summary=旧摘要"]


def test_save_semantic_memories_and_profile_save_follow_filters() -> None:
    milvus_ltm = FakeLongTermMemory()
    milvus_ltm.deduplicate_results = [False, True]
    writer_calls: list[tuple[int, dict, object | None]] = []

    async def fake_profile_writer(user_id: int, profile: dict, redis_client: object | None):
        writer_calls.append((user_id, profile, redis_client))
        return True

    _run(
        save_semantic_memories(
            milvus_ltm=milvus_ltm,
            tenant_id="tenant-1",
            user_id="42",
            semantic_memories=[
                MemoryExtractorResult(memory_type="issue_history", content="已存在的记忆"),
                MemoryExtractorResult(memory_type="solution_note", content="需要落库的新记忆"),
            ],
        )
    )
    _run(
        save_profile_if_present(
            profile_writer=fake_profile_writer,
            profile_cache="cache",
            user_id="42",
            profile={"preferred_category": "智能门铃"},
        )
    )
    _run(
        save_profile_if_present(
            profile_writer=fake_profile_writer,
            profile_cache="cache",
            user_id="not-int",
            profile={"preferred_category": "不会写入"},
        )
    )

    assert milvus_ltm.deduplicate_calls == [
        ("tenant-1", "42", "issue_history", "已存在的记忆"),
        ("tenant-1", "42", "solution_note", "需要落库的新记忆"),
    ]
    assert milvus_ltm.saved_memories == [
        ("tenant-1", "42", "solution_note", "需要落库的新记忆")
    ]
    assert writer_calls == [(42, {"preferred_category": "智能门铃"}, "cache")]


def test_update_hit_long_term_memories_is_best_effort() -> None:
    milvus_ltm = FakeLongTermMemory()

    _run(
        update_hit_long_term_memories(
            milvus_ltm=milvus_ltm,
            long_term_memories=[
                MemorySearchResult(
                    memory=LongTermMemory(
                        memory_id="mem-ok",
                        tenant_id="tenant-1",
                        user_id="42",
                        memory_type="issue_history",
                        content="正常命中",
                    ),
                    score=0.9,
                ),
                MemorySearchResult(
                    memory=LongTermMemory(
                        memory_id="mem-fail",
                        tenant_id="tenant-1",
                        user_id="42",
                        memory_type="issue_history",
                        content="单条失败",
                    ),
                    score=0.8,
                ),
            ],
        )
    )

    assert milvus_ltm.updated_ids == ["mem-ok"]
