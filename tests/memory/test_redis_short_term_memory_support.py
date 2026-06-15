import asyncio

import app.knowledge.infrastructure.stm.redis_short_term_memory as stm_memory
from app.knowledge.domain.schemas import MessageRecord, SessionMeta, SessionSummary
from app.knowledge.infrastructure.stm.redis_short_term_memory import (
    run_compression_pipeline,
    CompressionContext,
    should_compress_session,
)


class FakeRedis:
    def __init__(self, *, lock_acquired: bool = True) -> None:
        self.lock_acquired = lock_acquired
        self.set_calls: list[dict] = []
        self.deleted_keys: list[str] = []

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        self.set_calls.append(
            {
                "key": key,
                "value": value,
                "ex": ex,
                "nx": nx,
            }
        )
        return self.lock_acquired

    async def delete(self, key: str) -> None:
        self.deleted_keys.append(key)


def _build_message(*, message_id: str, turn_index: int) -> MessageRecord:
    return MessageRecord(
        message_id=message_id,
        role="user" if turn_index % 2 else "assistant",
        content=f"content-{message_id}",
        created_at=turn_index,
        turn_index=turn_index,
    )


def _run(awaitable):
    return asyncio.run(awaitable)


def _build_settings() -> stm_memory.ShortTermMemoryRuntimeSettings:
    return stm_memory.RedisShortTermMemory(redis_client=object()).settings


def test_constructor_collects_nested_runtime_settings() -> None:
    settings = _build_settings()

    assert settings.key_prefix == "agent:stm"
    assert settings.ttl_seconds == 86400
    assert settings.max_messages == 16
    assert settings.trigger_rounds == 6
    assert settings.keep_recent_rounds == 4
    assert settings.time_window_seconds == 86400


def test_should_compress_session_respects_disable_and_thresholds() -> None:
    settings = _build_settings()

    assert should_compress_session(
        settings,
        total_turns=8,
        last_compressed_turn=1,
        message_count=2,
    ) is True
    assert should_compress_session(
        settings,
        total_turns=2,
        last_compressed_turn=1,
        message_count=20,
    ) is True
    disabled_settings = settings.__class__(**{**settings.__dict__, "compression_enabled": False})
    assert should_compress_session(
        disabled_settings,
        total_turns=100,
        last_compressed_turn=0,
        message_count=100,
    ) is False


def test_compress_session_memory_builds_pipeline_context() -> None:
    redis_client = object()
    stm = stm_memory.RedisShortTermMemory(redis_client=redis_client)
    meta = SessionMeta(total_turns=8, last_updated_at=0, last_compressed_turn=1)
    summary = SessionSummary(content="旧摘要", compressed_at=12, compressed_round=4)
    messages = [_build_message(message_id=f"msg-{index}", turn_index=index) for index in range(1, 11)]
    captured: dict[str, object] = {}

    async def fake_get_meta(*_args) -> SessionMeta:
        return meta

    async def fake_get_message_count(*_args) -> int:
        return len(messages)

    async def fake_get_summary(*_args) -> SessionSummary:
        return summary

    async def fake_get_recent_messages(*_args, **_kwargs) -> list[MessageRecord]:
        return messages

    async def fake_llm_compress(_summary: str, _messages: list[MessageRecord]) -> str:
        raise AssertionError("should not be called in this test")

    async def fake_run_compression_pipeline(**kwargs) -> bool:
        captured.update(kwargs)
        return True

    stm.get_meta = fake_get_meta  # type: ignore[method-assign]
    stm.get_message_count = fake_get_message_count  # type: ignore[method-assign]
    stm.get_summary = fake_get_summary  # type: ignore[method-assign]
    stm.get_recent_messages = fake_get_recent_messages  # type: ignore[method-assign]
    original_run_pipeline = stm_memory.run_compression_pipeline
    stm_memory.run_compression_pipeline = fake_run_compression_pipeline
    try:
        compressed = _run(
            stm.compress_session_memory(
                "tenant-1",
                "user-1",
                "session-1",
                fake_llm_compress,
            )
        )
    finally:
        stm_memory.run_compression_pipeline = original_run_pipeline

    assert compressed is True
    assert captured["redis_client"] is redis_client
    assert captured["lock_ttl_seconds"] == stm.settings.lock_ttl_seconds
    context = captured["context"]
    assert isinstance(context, CompressionContext)
    assert context.keys == stm_memory.build_session_keys(
        "agent:stm",
        "tenant-1",
        "user-1",
        "session-1",
    )
    assert context.old_summary_str == summary.model_dump_json()
    assert [message.message_id for message in context.messages_to_compress] == ["msg-1", "msg-2"]
    assert [message.message_id for message in context.messages_to_keep] == [
        "msg-3",
        "msg-4",
        "msg-5",
        "msg-6",
        "msg-7",
        "msg-8",
        "msg-9",
        "msg-10",
    ]


def test_compress_session_memory_returns_false_when_threshold_not_met() -> None:
    stm = stm_memory.RedisShortTermMemory(redis_client=object())

    async def fake_get_meta(*_args) -> SessionMeta:
        return SessionMeta(total_turns=2, last_updated_at=0, last_compressed_turn=1)

    async def fake_get_message_count(*_args) -> int:
        return 1

    async def fake_get_summary(*_args):
        return None

    async def fake_get_recent_messages(*_args, **_kwargs) -> list[MessageRecord]:
        return []

    async def fake_llm_compress(_summary: str, _messages: list[MessageRecord]) -> str:
        raise AssertionError("should not be called in this test")

    async def fake_run_compression_pipeline(**_kwargs) -> bool:
        raise AssertionError("should not be called in this test")

    stm.get_meta = fake_get_meta  # type: ignore[method-assign]
    stm.get_message_count = fake_get_message_count  # type: ignore[method-assign]
    stm.get_summary = fake_get_summary  # type: ignore[method-assign]
    stm.get_recent_messages = fake_get_recent_messages  # type: ignore[method-assign]
    original_run_pipeline = stm_memory.run_compression_pipeline
    stm_memory.run_compression_pipeline = fake_run_compression_pipeline
    try:
        compressed = _run(
            stm.compress_session_memory(
                "tenant-1",
                "user-1",
                "session-1",
                fake_llm_compress,
            )
        )
    finally:
        stm_memory.run_compression_pipeline = original_run_pipeline

    assert compressed is False


def test_run_compression_pipeline_updates_meta_and_releases_lock() -> None:
    redis_client = FakeRedis(lock_acquired=True)
    keys = stm_memory.build_session_keys("agent:stm", "tenant-1", "user-1", "session-1")
    context = CompressionContext(
        keys=keys,
        meta=SessionMeta(total_turns=9, last_updated_at=0, last_compressed_turn=2),
        old_summary_str="",
        messages_to_compress=[],
        messages_to_keep=[],
    )
    calls: list[str] = []
    saved_meta: list[SessionMeta] = []

    async def update_summary(_context: CompressionContext) -> None:
        calls.append("summary")

    async def rewrite_messages(_context: CompressionContext) -> None:
        calls.append("rewrite")

    async def save_meta(meta: SessionMeta) -> None:
        calls.append("meta")
        saved_meta.append(meta)

    compressed = _run(
        run_compression_pipeline(
            redis_client=redis_client,
            context=context,
            lock_ttl_seconds=10,
            update_summary=update_summary,
            rewrite_messages=rewrite_messages,
            save_meta=save_meta,
        )
    )

    assert compressed is True
    assert calls == ["summary", "rewrite", "meta"]
    assert saved_meta == [
        SessionMeta(total_turns=9, last_updated_at=0, last_compressed_turn=9)
    ]
    assert redis_client.set_calls == [
        {"key": keys["lock"], "value": "1", "ex": 10, "nx": True}
    ]
    assert redis_client.deleted_keys == [keys["lock"]]


def test_run_compression_pipeline_returns_false_when_lock_not_acquired() -> None:
    redis_client = FakeRedis(lock_acquired=False)
    keys = stm_memory.build_session_keys("agent:stm", "tenant-1", "user-1", "session-1")
    context = CompressionContext(
        keys=keys,
        meta=SessionMeta(total_turns=9, last_updated_at=0, last_compressed_turn=2),
        old_summary_str="",
        messages_to_compress=[],
        messages_to_keep=[],
    )

    async def _noop(_context):
        raise AssertionError("should not be called")

    async def _save_meta(_meta):
        raise AssertionError("should not be called")

    compressed = _run(
        run_compression_pipeline(
            redis_client=redis_client,
            context=context,
            lock_ttl_seconds=10,
            update_summary=_noop,
            rewrite_messages=_noop,
            save_meta=_save_meta,
        )
    )

    assert compressed is False
    assert redis_client.deleted_keys == []
