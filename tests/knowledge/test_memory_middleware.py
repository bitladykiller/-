import asyncio

import app.knowledge.infrastructure.orchestration.memory_middleware as memory_middleware
from app.knowledge.domain.schemas import (
    LongTermMemory,
    MemoryExtractorResult,
    MemorySearchResult,
    MessageRecord,
    SessionMeta,
    SessionSummary,
)
from app.knowledge.infrastructure.orchestration.memory_middleware import MemoryMiddleware


class FakeLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.debugs: list[str] = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def debug(self, message: str) -> None:
        self.debugs.append(message)


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
        self.deduplicate_calls: list[tuple[str, str, str, str, str]] = []
        self.saved_memories: list[tuple[str, str, str, str, str]] = []
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
        *,
        session_id: str = "",
    ) -> str:
        self.saved_memories.append((tenant_id, user_id, memory_type, content, session_id))
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


def test_before_agent_degrades_and_warns_once_on_memory_load_failures(monkeypatch) -> None:
    class BrokenRedisShortTermMemory(FakeRedisShortTermMemory):
        async def get_summary(self, tenant_id: str, user_id: str, session_id: str) -> SessionSummary:
            raise RuntimeError("redis failed")

    class BrokenLongTermMemory(FakeLongTermMemory):
        async def hybrid_search(
            self,
            tenant_id: str,
            user_id: str,
            user_input: str,
        ) -> list[MemorySearchResult]:
            raise RuntimeError("milvus failed")

    logger = FakeLogger()
    monkeypatch.setattr(memory_middleware, "logger", logger)

    async def broken_profile_reader(user_id: int, redis_client: object | None):
        raise RuntimeError("profile failed")

    middleware = MemoryMiddleware(
        redis_stm=BrokenRedisShortTermMemory(should_compress_result=False),
        milvus_ltm=BrokenLongTermMemory(),
        memory_extractor=FakeMemoryExtractor(),
        profile_reader=broken_profile_reader,
    )

    first = _run(middleware.before_agent("tenant-1", "42", "session-1", "怎么修空调"))
    second = _run(middleware.before_agent("tenant-1", "42", "session-1", "怎么修空调"))

    assert first.session_summary is None
    assert first.recent_messages == []
    assert first.user_profile == {}
    assert first.long_term_memories == []
    assert second.session_summary is None
    assert second.recent_messages == []
    assert second.user_profile == {}
    assert second.long_term_memories == []
    assert logger.warnings == [
        "[memory] Redis STM 读取失败（未知错误），短期记忆降级",
        "[memory] 用户画像读取失败（未知错误），降级为空画像",
        "[memory] Milvus LTM 检索失败（未知错误），长期记忆降级",
    ]


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
        ("tenant-1", "5", "solution_note", "建议先检查电源和 WiFi", "session-1")
    ]
    assert milvus_ltm.updated_memory_ids == ["mem-hit"]
    assert profile_writer_calls == [
        (5, {"preferred_category": "智能门铃"}, redis_stm.redis)
    ]


def test_after_agent_logs_profile_write_failure_without_aborting(monkeypatch) -> None:
    logger = FakeLogger()
    monkeypatch.setattr(memory_middleware, "logger", logger)
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

    async def broken_profile_writer(user_id: int, profile: dict, redis_client: object | None):
        raise RuntimeError("profile failed")

    middleware = MemoryMiddleware(
        redis_stm=redis_stm,
        milvus_ltm=milvus_ltm,
        memory_extractor=extractor,
        profile_writer=broken_profile_writer,
    )

    _run(
        middleware.after_agent(
            "tenant-1",
            "5",
            "session-1",
            "门铃连不上网",
            "你可以先检查一下 WiFi 和电源",
        )
    )

    assert milvus_ltm.deduplicate_calls == [
        ("tenant-1", "5", "solution_note", "建议先检查电源和 WiFi")
    ]
    assert milvus_ltm.saved_memories == [
        ("tenant-1", "5", "solution_note", "建议先检查电源和 WiFi", "session-1")
    ]
    assert logger.debugs == ["[memory] 用户画像更新失败(user_id=5): profile failed"]


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


def test_after_agent_updates_hits_best_effort() -> None:
    class PartiallyFailingLongTermMemory(FakeLongTermMemory):
        async def update_memory_hit_info(self, memory: LongTermMemory) -> bool:
            if memory.memory_id == "mem-fail":
                raise RuntimeError("boom")
            self.updated_memory_ids.append(memory.memory_id)
            return True

    redis_stm = FakeRedisShortTermMemory(should_compress_result=False)
    milvus_ltm = PartiallyFailingLongTermMemory()
    middleware = MemoryMiddleware(
        redis_stm=redis_stm,
        milvus_ltm=milvus_ltm,
        memory_extractor=FakeMemoryExtractor(),
    )

    _run(
        middleware.after_agent(
            "tenant-1",
            "5",
            "session-1",
            "门铃连不上网",
            "你可以先检查一下 WiFi 和电源",
            [
                MemorySearchResult(
                    memory=LongTermMemory(
                        memory_id="mem-ok",
                        tenant_id="tenant-1",
                        user_id="5",
                        memory_type="issue_history",
                        content="正常命中",
                    ),
                    score=0.9,
                ),
                MemorySearchResult(
                    memory=LongTermMemory(
                        memory_id="mem-fail",
                        tenant_id="tenant-1",
                        user_id="5",
                        memory_type="issue_history",
                        content="单条失败",
                    ),
                    score=0.8,
                ),
            ],
        )
    )

    assert milvus_ltm.updated_memory_ids == ["mem-ok"]
