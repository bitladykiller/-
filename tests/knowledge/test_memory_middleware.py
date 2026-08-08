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
    """记录降级日志。

    `exceptions` 单独收集：命中它说明 log_degradation 判定为"疑似代码缺陷"
    并打了堆栈，是我们希望缺陷不再被静默吞掉的关键信号。
    """

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.debugs: list[str] = []
        self.exceptions: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message % args if args else message)

    def debug(self, message: str, *args: object) -> None:
        self.debugs.append(message % args if args else message)

    def exception(self, message: str, *args: object) -> None:
        self.exceptions.append(message % args if args else message)


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
            should_compress_result if compress_session_result is None else compress_session_result
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
        self.append_messages_calls = 0
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

    async def append_messages(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        messages,
    ) -> None:
        self.append_messages_calls += 1
        self.appended_messages.extend(messages)

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
        *,
        compression_id: str = "",
    ) -> bool:
        self.compress_calls += 1
        self.compression_id = compression_id
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
        self.hit_update_batches = 0

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
        return await self.update_memory_hit_infos([memory])

    async def update_memory_hit_infos(self, memories) -> bool:
        self.hit_update_batches += 1
        self.updated_memory_ids.extend(memory.memory_id for memory in memories)
        return True

    async def update_memory_hit_infos_deduped(
        self,
        memories,
        *,
        turn_id: str = "",
    ) -> bool:
        self.hit_update_batches += 1
        self.updated_memory_ids.extend(memory.memory_id for memory in memories)
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
    profile_reader_calls: list[tuple[str, int, object]] = []

    async def fake_profile_reader(tenant_id: str, user_id: int, redis_client: object | None):
        profile_reader_calls.append((tenant_id, user_id, redis_client))
        return expected_profile

    middleware = MemoryMiddleware(
        redis_stm=redis_stm,
        milvus_ltm=milvus_ltm,
        memory_extractor=extractor,
        profile_reader=fake_profile_reader,
    )

    memory_state = _run(middleware.before_agent("tenant-1", "42", "session-1", "怎么修空调"))

    assert memory_state.session_summary == redis_stm.summary
    assert memory_state.recent_messages == redis_stm.recent_messages
    assert memory_state.long_term_memories == [expected_memory]
    assert memory_state.user_profile == expected_profile
    assert profile_reader_calls == [("tenant-1", 42, redis_stm.redis)]
    assert milvus_ltm.hybrid_search_calls == [("tenant-1", "42", "怎么修空调")]


def test_before_agent_degrades_and_warns_once_on_memory_load_failures(monkeypatch) -> None:
    class BrokenRedisShortTermMemory(FakeRedisShortTermMemory):
        async def get_summary(
            self, tenant_id: str, user_id: str, session_id: str
        ) -> SessionSummary:
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

    async def broken_profile_reader(tenant_id: str, user_id: int, redis_client: object | None):
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
    # 三路都是 RuntimeError（非外部依赖故障）→ 判定为疑似代码缺陷，
    # 走 logger.exception 带堆栈，而不是一句没有异常信息的 warning。
    # 并发读取导致到达顺序不确定，因此只断言集合。
    assert len(logger.exceptions) == 3
    assert logger.warnings == []
    operations = sorted(entry.split(" 降级")[0] for entry in logger.exceptions)
    assert operations == [
        "memory.read_long_term",
        "memory.read_short_term",
        "memory.read_user_profile",
    ]
    assert all("疑似代码缺陷" in entry for entry in logger.exceptions)


def test_before_agent_treats_redis_outage_as_expected_degradation(monkeypatch) -> None:
    """外部依赖故障走 warning，不该被当成代码缺陷告警。"""
    import redis.exceptions as redis_exceptions

    class OutageRedis(FakeRedisShortTermMemory):
        async def get_summary(self, tenant_id, user_id, session_id):
            raise redis_exceptions.ConnectionError("redis down")

    async def ok_profile_reader(tenant_id: str, user_id: int, redis_client: object | None):
        return {}

    logger = FakeLogger()
    monkeypatch.setattr(memory_middleware, "logger", logger)
    middleware = MemoryMiddleware(
        redis_stm=OutageRedis(should_compress_result=False),
        milvus_ltm=FakeLongTermMemory(),
        memory_extractor=FakeMemoryExtractor(),
        profile_reader=ok_profile_reader,
    )

    state = _run(middleware.before_agent("tenant-1", "42", "session-1", "怎么修空调"))

    assert state.recent_messages == []
    assert logger.exceptions == []
    assert len(logger.warnings) == 1
    assert "memory.read_short_term" in logger.warnings[0]


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
    profile_writer_calls: list[tuple[str, int, dict, object, str | None]] = []

    async def fake_profile_writer(
        tenant_id: str,
        user_id: int,
        profile: dict,
        redis_client: object | None,
        source_turn_id: str | None,
    ):
        profile_writer_calls.append((tenant_id, user_id, profile, redis_client, source_turn_id))
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
        ("tenant-1", 5, {"preferred_category": "智能门铃"}, redis_stm.redis, None)
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

    async def broken_profile_writer(
        tenant_id: str,
        user_id: int,
        profile: dict,
        redis_client: object | None,
        source_turn_id: str | None,
    ):
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
    assert len(logger.exceptions) == 1
    assert "memory.write_user_profile" in logger.exceptions[0]
    assert "profile failed" in logger.exceptions[0]


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
    profile_writer_calls: list[tuple[str, int, dict, object, str | None]] = []

    async def fake_profile_writer(
        tenant_id: str,
        user_id: int,
        profile: dict,
        redis_client: object | None,
        source_turn_id: str | None,
    ):
        profile_writer_calls.append((tenant_id, user_id, profile, redis_client, source_turn_id))
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


def _hit_results(*memory_ids: str) -> list[MemorySearchResult]:
    return [
        MemorySearchResult(
            memory=LongTermMemory(
                memory_id=memory_id,
                tenant_id="tenant-1",
                user_id="5",
                memory_type="issue_history",
                content=f"命中 {memory_id}",
            ),
            score=0.9,
        )
        for memory_id in memory_ids
    ]


def test_after_agent_refreshes_all_hits_in_one_batch() -> None:
    milvus_ltm = FakeLongTermMemory()
    middleware = MemoryMiddleware(
        redis_stm=FakeRedisShortTermMemory(should_compress_result=False),
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
            _hit_results("mem-a", "mem-b", "mem-c"),
        )
    )

    assert milvus_ltm.updated_memory_ids == ["mem-a", "mem-b", "mem-c"]
    # 关键断言：3 条命中只走 1 次批量刷新，而不是逐条往返 Milvus
    assert milvus_ltm.hit_update_batches == 1


def test_after_agent_swallows_hit_refresh_failure(monkeypatch) -> None:
    """命中统计是旁路逻辑：整批刷新失败也不能让 after_agent 抛出。"""

    class BrokenLongTermMemory(FakeLongTermMemory):
        async def update_memory_hit_infos_deduped(
            self,
            memories,
            *,
            turn_id: str = "",
        ) -> bool:
            raise RuntimeError("boom")

    logger = FakeLogger()
    monkeypatch.setattr(memory_middleware, "logger", logger)
    middleware = MemoryMiddleware(
        redis_stm=FakeRedisShortTermMemory(should_compress_result=False),
        milvus_ltm=BrokenLongTermMemory(),
        memory_extractor=FakeMemoryExtractor(),
    )

    _run(
        middleware.after_agent(
            "tenant-1",
            "5",
            "session-1",
            "门铃连不上网",
            "你可以先检查一下 WiFi 和电源",
            _hit_results("mem-ok", "mem-fail"),
        )
    )

    assert len(logger.exceptions) == 1
    assert "memory.refresh_ltm_hits" in logger.exceptions[0]


async def test_before_agent_reads_all_sources_concurrently() -> None:
    """三路记忆读取必须并发。

    STM / 画像 / LTM 落在三套不同存储上，互不依赖。串行会把每轮对话的
    记忆加载耗时变成三者之和——其中 LTM 还要跑 embedding + 向量检索。
    这里让每一路都阻塞到"三路都已进场"，串行实现会直接超时。
    """
    # 手写 barrier：asyncio.Barrier 要 Python 3.11，本项目下限是 3.10
    arrived = 0
    all_arrived = asyncio.Event()

    async def rendezvous() -> None:
        nonlocal arrived
        arrived += 1
        if arrived == 3:
            all_arrived.set()
        # 串行实现下第一路会在这里等到超时，因为后两路根本还没开始
        await asyncio.wait_for(all_arrived.wait(), timeout=2)

    class ConcurrentRedis(FakeRedisShortTermMemory):
        async def get_summary(self, tenant_id, user_id, session_id):
            await rendezvous()
            return self.summary

    class ConcurrentLongTerm(FakeLongTermMemory):
        async def hybrid_search(self, tenant_id, user_id, user_input):
            await rendezvous()
            return []

    async def concurrent_profile_reader(
        tenant_id: str,
        user_id: int,
        redis_client: object | None,
    ):
        await rendezvous()
        return {"preferred_brand": "小米"}

    middleware = MemoryMiddleware(
        redis_stm=ConcurrentRedis(should_compress_result=False),
        milvus_ltm=ConcurrentLongTerm(),
        memory_extractor=FakeMemoryExtractor(),
        profile_reader=concurrent_profile_reader,
    )

    state = await middleware.before_agent("tenant-1", "42", "session-1", "怎么修空调")

    assert state.user_profile == {"preferred_brand": "小米"}
    assert state.long_term_memories == []


async def test_after_agent_reuses_meta_without_second_read() -> None:
    """第 1 段已经拿到并写回 meta，第 2 段不该再读一次。"""

    class CountingRedis(FakeRedisShortTermMemory):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.get_meta_calls = 0

        async def get_meta(self, tenant_id, user_id, session_id):
            self.get_meta_calls += 1
            return self.meta

    redis_stm = CountingRedis(should_compress_result=False)
    middleware = MemoryMiddleware(
        redis_stm=redis_stm,
        milvus_ltm=FakeLongTermMemory(),
        memory_extractor=FakeMemoryExtractor(),
    )

    await middleware.after_agent("tenant-1", "5", "session-1", "问题", "回答")

    assert redis_stm.get_meta_calls == 1
