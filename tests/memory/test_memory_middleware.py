import asyncio

from app.knowledge.domain.schemas import (
    LongTermMemory,
    MemoryExtractorResult,
    MemorySearchResult,
    MessageRecord,
    SessionMeta,
    SessionSummary,
)
from app.knowledge.infrastructure.orchestration.memory_middleware import MemoryMiddleware


class FakeRedisShortTermMemory:
    def __init__(
        self,
        *,
        should_compress_result: bool,
        compress_session_result: bool | None = None,
    ) -> None:
        self.redis = object()
        self.should_compress_result = should_compress_result
        self.compress_session_result = (
            should_compress_result
            if compress_session_result is None
            else compress_session_result
        )
        self.summary = SessionSummary(
            content="历史摘要",
            compressed_at=1,
            compressed_round=1,
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
        self.meta = SessionMeta(total_turns=1, last_updated_at=0, last_compressed_turn=0)
        self.appended_messages: list[MessageRecord] = []
        self.saved_meta: SessionMeta | None = None
        self.refresh_calls = 0
        self.should_compress_args: tuple[int, int, int] | None = None
        self.compress_calls = 0
        self.summary_callback_result: str | None = None

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

    async def get_message_count(self, tenant_id: str, user_id: str, session_id: str) -> int:
        return len(self.appended_messages)

    def should_compress(
        self,
        total_turns: int,
        last_compressed_turn: int,
        msg_count: int,
    ) -> bool:
        self.should_compress_args = (total_turns, last_compressed_turn, msg_count)
        return self.should_compress_result

    async def compress_session_memory(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        summary_compressor,
    ) -> bool:
        self.compress_calls += 1
        if not self.compress_session_result:
            return False
        self.summary_callback_result = await summary_compressor(
            self.summary.content,
            self.recent_messages,
        )
        return True


class FakeLongTermMemory:
    def __init__(self) -> None:
        self.hybrid_results: list[MemorySearchResult] = []
        self.hybrid_search_calls: list[tuple[str, str, str]] = []
        self.deduplicate_calls: list[tuple[str, str, str, str]] = []
        self.saved_memories: list[tuple[str, str, str, str]] = []
        self.updated_memory_ids: list[str] = []

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
        return True

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
        self.updated_memory_ids.append(memory.memory_id)
        return True


class FakeLLMClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return '{"content":"压缩摘要"}'


class FakeMemoryExtractor:
    def __init__(
        self,
        *,
        semantic_memories: list[MemoryExtractorResult] | None = None,
        profile: dict | None = None,
    ) -> None:
        self.llm_client = FakeLLMClient()
        self.semantic_memories = semantic_memories or []
        self.profile = profile or {}
        self.extract_calls: list[tuple[str, str, SessionSummary | None]] = []

    async def extract(
        self,
        user_message: str,
        assistant_message: str,
        session_summary: SessionSummary | None = None,
    ) -> tuple[list[MemoryExtractorResult], dict]:
        self.extract_calls.append((user_message, assistant_message, session_summary))
        return self.semantic_memories, self.profile


def _run(awaitable):
    return asyncio.run(awaitable)


def test_before_agent_loads_all_memory_layers() -> None:
    redis_stm = FakeRedisShortTermMemory(should_compress_result=False)
    milvus_ltm = FakeLongTermMemory()
    expected_profile = {"preferred_brand": "海尔", "tags": ["家电"]}
    expected_memory = MemorySearchResult(
        memory=LongTermMemory(
            memory_id="mem-1",
            tenant_id="tenant-1",
            user_id="42",
            memory_type="issue_history",
            content="曾问过空调维修",
        ),
        score=0.91,
    )
    milvus_ltm.hybrid_results = [expected_memory]
    extractor = FakeMemoryExtractor()
    profile_reader_calls: list[tuple[int, object]] = []

    async def fake_profile_reader(user_id: int, redis_client: object | None):
        profile_reader_calls.append((user_id, redis_client))
        return expected_profile

    middleware = MemoryMiddleware(
        redis_stm=redis_stm,
        milvus_ltm=milvus_ltm,
        memory_extractor=extractor,
        profile_reader=fake_profile_reader,
    )

    memory_state = _run(
        middleware.before_agent("tenant-1", "42", "session-1", "怎么修空调")
    )

    assert memory_state.session_summary == redis_stm.summary
    assert memory_state.recent_messages == redis_stm.recent_messages
    assert memory_state.long_term_memories == [expected_memory]
    assert memory_state.user_profile == expected_profile
    assert profile_reader_calls == [(42, redis_stm.redis)]
    assert milvus_ltm.hybrid_search_calls == [("tenant-1", "42", "怎么修空调")]


def test_after_agent_persists_turn_extracts_memory_and_updates_hits() -> None:
    redis_stm = FakeRedisShortTermMemory(should_compress_result=True)
    milvus_ltm = FakeLongTermMemory()
    extractor = FakeMemoryExtractor(
        semantic_memories=[
            MemoryExtractorResult(
                memory_type="solution_note",
                content="建议先检查电源和 WiFi",
            )
        ],
        profile={"preferred_category": "智能门铃"},
    )
    profile_writer_calls: list[tuple[int, dict, object]] = []

    async def fake_profile_writer(user_id: int, profile: dict, redis_client: object | None):
        profile_writer_calls.append((user_id, profile, redis_client))
        return True

    middleware = MemoryMiddleware(
        redis_stm=redis_stm,
        milvus_ltm=milvus_ltm,
        memory_extractor=extractor,
        profile_writer=fake_profile_writer,
    )
    hit_memory = MemorySearchResult(
        memory=LongTermMemory(
            memory_id="mem-hit",
            tenant_id="tenant-1",
            user_id="5",
            memory_type="issue_history",
            content="门铃连不上网",
        ),
        score=0.88,
    )

    _run(
        middleware.after_agent(
            "tenant-1",
            "5",
            "session-1",
            "门铃连不上网",
            "你可以先检查一下 WiFi 和电源",
            [hit_memory],
        )
    )

    assert len(redis_stm.appended_messages) == 2
    assert [message.role for message in redis_stm.appended_messages] == [
        "user",
        "assistant",
    ]
    assert redis_stm.saved_meta is not None
    assert redis_stm.saved_meta.total_turns == 2
    assert redis_stm.refresh_calls == 1
    assert redis_stm.should_compress_args is not None
    assert redis_stm.compress_calls == 1
    assert redis_stm.summary_callback_result == '{"content":"压缩摘要"}'
    assert extractor.extract_calls == [
        ("门铃连不上网", "你可以先检查一下 WiFi 和电源", redis_stm.summary)
    ]
    assert milvus_ltm.deduplicate_calls == [
        ("tenant-1", "5", "solution_note", "建议先检查电源和 WiFi")
    ]
    assert milvus_ltm.saved_memories == [
        ("tenant-1", "5", "solution_note", "建议先检查电源和 WiFi")
    ]
    assert milvus_ltm.updated_memory_ids == ["mem-hit"]
    assert profile_writer_calls == [
        (5, {"preferred_category": "智能门铃"}, redis_stm.redis)
    ]


def test_after_agent_skips_extraction_when_compress_did_not_complete() -> None:
    redis_stm = FakeRedisShortTermMemory(
        should_compress_result=True,
        compress_session_result=False,
    )
    milvus_ltm = FakeLongTermMemory()
    extractor = FakeMemoryExtractor(
        semantic_memories=[
            MemoryExtractorResult(
                memory_type="solution_note",
                content="这条记忆不该被落库",
            )
        ],
        profile={"preferred_brand": "不应写入"},
    )
    profile_writer_calls: list[tuple[int, dict, object]] = []

    async def fake_profile_writer(user_id: int, profile: dict, redis_client: object | None):
        profile_writer_calls.append((user_id, profile, redis_client))
        return True

    middleware = MemoryMiddleware(
        redis_stm=redis_stm,
        milvus_ltm=milvus_ltm,
        memory_extractor=extractor,
        profile_writer=fake_profile_writer,
    )

    _run(
        middleware.after_agent(
            "tenant-1",
            "5",
            "session-1",
            "这轮虽然达到阈值",
            "但压缩没有真的成功",
        )
    )

    assert redis_stm.compress_calls == 1
    assert redis_stm.summary_callback_result is None
    assert extractor.extract_calls == []
    assert milvus_ltm.deduplicate_calls == []
    assert milvus_ltm.saved_memories == []
    assert profile_writer_calls == []
