import asyncio

import app.knowledge.infrastructure.orchestration.memory_middleware as memory_middleware
from app.knowledge.domain.schemas import (
    AgentMemoryState,
    LongTermMemory,
    MemoryExtractorResult,
    MemorySearchResult,
    SessionMeta,
    SessionSummary,
)


class FakeLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.debugs: list[str] = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def debug(self, message: str) -> None:
        self.debugs.append(message)


class FakeRedisSTM:
    def __init__(self, *, should_compress_result: bool) -> None:
        self.redis = object()
        self.should_compress_result = should_compress_result
        self.meta = SessionMeta(total_turns=3, last_updated_at=0, last_compressed_turn=1)
        self.message_count = 6
        self.compress_calls: list[dict] = []

    async def get_meta(self, tenant_id: str, user_id: str, session_id: str) -> SessionMeta:
        return self.meta

    async def get_message_count(self, tenant_id: str, user_id: str, session_id: str) -> int:
        return self.message_count

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
        self.compress_calls.append(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "session_id": session_id,
                "summary_compressor": summary_compressor,
            }
        )
        return True


def _run(awaitable):
    return asyncio.run(awaitable)


def test_warn_once_only_logs_first_occurrence() -> None:
    logger = FakeLogger()
    errors_warned: set[str] = set()

    memory_middleware.warn_once(errors_warned, key="redis_stm", message="warn-1", logger=logger)
    memory_middleware.warn_once(errors_warned, key="redis_stm", message="warn-2", logger=logger)

    assert errors_warned == {"redis_stm"}
    assert logger.warnings == ["warn-1"]


def test_profile_cache_returns_redis_attribute_when_present() -> None:
    stm = FakeRedisSTM(should_compress_result=False)

    assert memory_middleware.profile_cache(stm) is stm.redis
    assert memory_middleware.profile_cache(object()) is None


def test_load_helpers_apply_fallbacks_on_error() -> None:
    memory_state = AgentMemoryState()
    warnings: list[tuple[str, str]] = []

    def fake_warn_once(key: str, message: str) -> None:
        warnings.append((key, message))

    async def broken_short_term_loader(*args, **kwargs) -> None:
        raise RuntimeError("boom")

    async def broken_profile_loader(*args, **kwargs) -> None:
        raise RuntimeError("boom")

    _run(
        memory_middleware.load_short_term_memory_safely(
            memory_state,
            redis_stm=object(),
            tenant_id="tenant-1",
            user_id="user-1",
            session_id="session-1",
            load_short_term_memory_state=broken_short_term_loader,
            warn_once=fake_warn_once,
        )
    )
    _run(
        memory_middleware.load_user_profile_safely(
            memory_state,
            profile_reader=object(),
            profile_cache="cache",
            user_id="user-1",
            load_user_profile_state=broken_profile_loader,
            warn_once=fake_warn_once,
        )
    )

    assert memory_state.session_summary is None
    assert memory_state.recent_messages == []
    assert memory_state.user_profile == {}
    assert warnings == [
        ("redis_stm_read", "[memory] Redis STM 读取失败，短期记忆降级"),
        ("user_profile", "[memory] 用户画像读取失败，降级为空画像"),
    ]


def test_compress_short_term_memory_if_needed_uses_total_turns_as_round() -> None:
    stm = FakeRedisSTM(should_compress_result=True)
    built_rounds: list[int] = []

    def fake_build_summary_compressor(compressed_round: int) -> str:
        built_rounds.append(compressed_round)
        return f"compressor-{compressed_round}"

    compressed = _run(
        memory_middleware.compress_short_term_memory_if_needed(
            redis_stm=stm,
            tenant_id="tenant-1",
            user_id="user-1",
            session_id="session-1",
            build_summary_compressor=fake_build_summary_compressor,
        )
    )

    assert compressed is True
    assert stm.should_compress_args == (3, 1, 6)
    assert built_rounds == [3]
    assert stm.compress_calls == [
        {
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "session_id": "session-1",
            "summary_compressor": "compressor-3",
        }
    ]


def test_extract_and_update_helpers_delegate_and_swallow_profile_or_hit_failures() -> None:
    logger = FakeLogger()
    saved_profiles: list[dict] = []
    saved_semantic_batches: list[list[MemoryExtractorResult]] = []
    hit_update_calls: list[list[MemorySearchResult]] = []
    warnings: list[tuple[str, str]] = []
    hit_memory = MemorySearchResult(
        memory=LongTermMemory(
            memory_id="mem-1",
            tenant_id="tenant-1",
            user_id="user-1",
            memory_type="issue_history",
            content="门铃掉线",
        ),
        score=0.8,
    )

    class FakeExtractor:
        async def extract(self, user_message: str, assistant_message: str, session_summary):
            assert user_message == "用户问题"
            assert assistant_message == "助手回复"
            assert session_summary == SessionSummary(content="摘要")
            return (
                [MemoryExtractorResult(memory_type="solution_note", content="建议重启")],
                {"preferred_brand": "海尔"},
            )

    async def fake_save_semantic_memories(**kwargs) -> None:
        saved_semantic_batches.append(kwargs["semantic_memories"])

    async def broken_profile_save(**kwargs) -> None:
        saved_profiles.append(kwargs["profile"])
        raise RuntimeError("profile failed")

    def fake_warn_once(key: str, message: str) -> None:
        warnings.append((key, message))

    async def broken_hit_update(**kwargs) -> None:
        hit_update_calls.append(kwargs["long_term_memories"])
        raise RuntimeError("hit failed")

    semantic_memories, profile = _run(
        memory_middleware.extract_and_save_long_term_memory(
            memory_extractor=FakeExtractor(),
            milvus_ltm=object(),
            profile_writer=object(),
            profile_cache="cache",
            tenant_id="tenant-1",
            user_id="user-1",
            user_message="用户问题",
            assistant_message="助手回复",
            session_summary=SessionSummary(content="摘要"),
            save_semantic_memories=fake_save_semantic_memories,
            save_profile_if_present=broken_profile_save,
            logger=logger,
        )
    )
    _run(
        memory_middleware.update_hit_long_term_memories_safely(
            milvus_ltm=object(),
            long_term_memories=[hit_memory],
            update_hit_long_term_memories=broken_hit_update,
            warn_once=fake_warn_once,
        )
    )

    assert [item.memory_type for item in semantic_memories] == ["solution_note"]
    assert profile == {"preferred_brand": "海尔"}
    assert saved_profiles == [{"preferred_brand": "海尔"}]
    assert len(saved_semantic_batches) == 1
    assert saved_semantic_batches[0][0].content == "建议重启"
    assert logger.debugs == ["[memory] 用户画像更新失败(user_id=user-1): profile failed"]
    assert hit_update_calls == [[hit_memory]]
    assert warnings == [("ltm_hit_update", "[memory] LTM 命中统计刷新失败")]
