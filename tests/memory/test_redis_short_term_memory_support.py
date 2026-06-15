import asyncio

import app.knowledge.infrastructure.stm.redis_short_term_memory as stm_memory
from app.knowledge.domain.schemas import MessageRecord, SessionMeta, SessionSummary
from app.knowledge.infrastructure.stm.stm_compressor import compress_message


class FakeRedis:
    def __init__(self, *, lock_acquired: bool = True) -> None:
        self.lock_acquired = lock_acquired
        self.values: dict[str, str | bytes] = {}
        self.set_calls: list[dict] = []
        self.get_calls: list[str] = []
        self.deleted_keys: list[str] = []
        self.zadd_calls: list[dict] = []
        self.zrevrange_calls: list[dict] = []
        self.zrevrange_results: dict[str, list[bytes | str]] = {}
        self.zcard_counts: dict[str, int] = {}
        self.zremrangebyrank_calls: list[dict] = []
        self.zremrangebyscore_calls: list[dict] = []
        self.expire_calls: list[dict] = []

    async def set(self, key: str, value: str, *, ex: int, nx: bool = False) -> bool:
        self.set_calls.append(
            {
                "key": key,
                "value": value,
                "ex": ex,
                "nx": nx,
            }
        )
        if nx and key.endswith(":lock"):
            return self.lock_acquired
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | bytes | None:
        self.get_calls.append(key)
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.deleted_keys.append(key)
        self.values.pop(key, None)

    async def zadd(self, key: str, mapping: dict[bytes | str, int]) -> None:
        self.zadd_calls.append({"key": key, "mapping": mapping})
        self.zcard_counts[key] = self.zcard_counts.get(key, 0) + len(mapping)

    async def zrevrange(self, key: str, start: int, end: int) -> list[bytes | str]:
        self.zrevrange_calls.append({"key": key, "start": start, "end": end})
        return self.zrevrange_results.get(key, [])

    async def zcard(self, key: str) -> int:
        return self.zcard_counts.get(key, 0)

    async def zremrangebyrank(self, key: str, start: int, stop: int) -> None:
        self.zremrangebyrank_calls.append({"key": key, "start": start, "stop": stop})

    async def zremrangebyscore(self, key: str, min_score: int, max_score: int) -> None:
        self.zremrangebyscore_calls.append(
            {"key": key, "min_score": min_score, "max_score": max_score}
        )

    async def expire(self, key: str, ttl: int) -> None:
        self.expire_calls.append({"key": key, "ttl": ttl})


def _build_message(
    *,
    message_label: str,
    role: str,
    created_at: int | None = None,
) -> MessageRecord:
    return MessageRecord(
        role=role,
        content=f"content-{message_label}",
        created_at=1 if created_at is None else created_at,
    )


def _run(awaitable):
    return asyncio.run(awaitable)


def _build_stm() -> stm_memory.RedisShortTermMemory:
    return stm_memory.RedisShortTermMemory(redis_client=object())


def test_build_session_keys_uses_consistent_suffixes() -> None:
    stm = _build_stm()
    keys = stm._build_session_keys("tenant-1", "user-1", "session-1")

    assert keys == {
        "messages": "agent:stm:tenant-1:user-1:session-1:messages",
        "summary": "agent:stm:tenant-1:user-1:session-1:summary",
        "meta": "agent:stm:tenant-1:user-1:session-1:meta",
        "lock": "agent:stm:tenant-1:user-1:session-1:lock",
    }


def test_constructor_collects_nested_runtime_settings() -> None:
    stm = _build_stm()

    assert stm.key_prefix == "agent:stm"
    assert stm.ttl_seconds == 86400
    assert stm.max_messages == 16
    assert stm.trigger_rounds == 6
    assert stm.keep_recent_rounds == 4
    assert stm.time_window_seconds == 86400


def test_should_compress_respects_thresholds() -> None:
    stm = _build_stm()

    assert stm.should_compress(8, 1, 2) is True
    assert stm.should_compress(2, 1, 20) is True
    assert stm.should_compress(2, 1, 1) is False


def test_append_message_persists_score_and_prunes_window(monkeypatch) -> None:
    redis_client = FakeRedis()
    stm = stm_memory.RedisShortTermMemory(redis_client=redis_client)
    monkeypatch.setattr(
        "app.knowledge.infrastructure.stm.redis_short_term_memory.time.time",
        lambda: 123.456,
    )
    message = _build_message(message_label="msg-1", role="user", created_at=1_700_000_000)

    _run(stm.append_message("tenant-1", "user-1", "session-1", message))

    key = stm._build_session_keys("tenant-1", "user-1", "session-1")["messages"]
    assert redis_client.zadd_calls[0]["key"] == key
    assert list(redis_client.zadd_calls[0]["mapping"].values()) == [123456]
    assert redis_client.zremrangebyrank_calls == [{"key": key, "start": 0, "stop": -17}]
    assert redis_client.zremrangebyscore_calls == [
        {"key": key, "min_score": 0, "max_score": -86276544}
    ]
    assert redis_client.expire_calls == [{"key": key, "ttl": 86400}]


def test_get_recent_messages_decodes_payloads_and_restores_order() -> None:
    redis_client = FakeRedis()
    stm = stm_memory.RedisShortTermMemory(redis_client=redis_client)
    old_message = _build_message(message_label="msg-1", role="user", created_at=1)
    new_message = _build_message(message_label="msg-2", role="assistant", created_at=2)
    key = stm._build_session_keys("tenant-1", "user-1", "session-1")["messages"]
    redis_client.zrevrange_results[key] = [
        compress_message(new_message),
        b"broken-payload",
        compress_message(old_message),
    ]

    decoded = _run(stm.get_recent_messages("tenant-1", "user-1", "session-1"))

    assert decoded == [old_message, new_message]


def test_get_summary_and_meta_decode_json_payloads() -> None:
    redis_client = FakeRedis()
    stm = stm_memory.RedisShortTermMemory(redis_client=redis_client)
    summary = SessionSummary(content="压缩后摘要")
    meta = SessionMeta(total_turns=3, last_compressed_turn=1)
    keys = stm._build_session_keys("tenant-1", "user-1", "session-1")

    redis_client.values[keys["summary"]] = summary.model_dump_json().encode("utf-8")
    redis_client.values[keys["meta"]] = meta.model_dump_json()

    assert _run(stm.get_summary("tenant-1", "user-1", "session-1")) == summary
    assert _run(stm.get_meta("tenant-1", "user-1", "session-1")) == meta

    redis_client.values[keys["summary"]] = '["not", "a", "dict"]'
    redis_client.values[keys["meta"]] = '["not", "a", "dict"]'

    assert _run(stm.get_summary("tenant-1", "user-1", "session-1")) is None
    assert _run(stm.get_meta("tenant-1", "user-1", "session-1")) == SessionMeta()


def test_compress_session_memory_splits_old_messages_and_persists_results() -> None:
    redis_client = FakeRedis(lock_acquired=True)
    stm = stm_memory.RedisShortTermMemory(redis_client=redis_client)
    meta = SessionMeta(total_turns=8, last_compressed_turn=1)
    summary = SessionSummary(content="旧摘要")
    messages = [
        _build_message(
            message_label=f"msg-{index}",
            role="user" if index % 2 else "assistant",
            created_at=index,
        )
        for index in range(1, 11)
    ]
    captured: dict[str, object] = {}

    async def fake_get_meta(*_args) -> SessionMeta:
        return meta

    async def fake_get_message_count(*_args) -> int:
        return len(messages)

    async def fake_get_summary(*_args) -> SessionSummary:
        return summary

    async def fake_get_recent_messages(*_args, **_kwargs) -> list[MessageRecord]:
        return messages

    async def fake_llm_compress(old_summary_str: str, old_messages: list[MessageRecord]) -> str:
        captured["summary"] = old_summary_str
        captured["message_contents"] = [message.content for message in old_messages]
        return '前置 {"content":"压缩后摘要"} 尾部'

    stm.get_meta = fake_get_meta  # type: ignore[method-assign]
    stm.get_message_count = fake_get_message_count  # type: ignore[method-assign]
    stm.get_summary = fake_get_summary  # type: ignore[method-assign]
    stm.get_recent_messages = fake_get_recent_messages  # type: ignore[method-assign]

    compressed = _run(
        stm.compress_session_memory(
            "tenant-1",
            "user-1",
            "session-1",
            fake_llm_compress,
        )
    )

    keys = stm._build_session_keys("tenant-1", "user-1", "session-1")
    assert compressed is True
    assert captured == {
        "summary": summary.model_dump_json(),
        "message_contents": ["content-msg-1", "content-msg-2"],
    }
    assert redis_client.deleted_keys == [keys["messages"], keys["lock"]]
    assert redis_client.values[keys["summary"]] == SessionSummary(content="压缩后摘要").model_dump_json()
    assert redis_client.values[keys["meta"]] == SessionMeta(
        total_turns=8,
        last_compressed_turn=8,
    ).model_dump_json()
    assert len(redis_client.zadd_calls) == 8


def test_compress_session_memory_returns_false_when_threshold_not_met() -> None:
    redis_client = FakeRedis()
    stm = stm_memory.RedisShortTermMemory(redis_client=redis_client)

    async def fake_get_meta(*_args) -> SessionMeta:
        return SessionMeta(total_turns=2, last_compressed_turn=1)

    async def fake_get_message_count(*_args) -> int:
        return 1

    async def fake_get_summary(*_args):
        return None

    async def fake_get_recent_messages(*_args, **_kwargs) -> list[MessageRecord]:
        return []

    async def fake_llm_compress(_summary: str, _messages: list[MessageRecord]) -> str:
        raise AssertionError("should not be called in this test")

    stm.get_meta = fake_get_meta  # type: ignore[method-assign]
    stm.get_message_count = fake_get_message_count  # type: ignore[method-assign]
    stm.get_summary = fake_get_summary  # type: ignore[method-assign]
    stm.get_recent_messages = fake_get_recent_messages  # type: ignore[method-assign]

    compressed = _run(
        stm.compress_session_memory(
            "tenant-1",
            "user-1",
            "session-1",
            fake_llm_compress,
        )
    )

    assert compressed is False
    assert redis_client.set_calls == []
    assert redis_client.zadd_calls == []


def test_compress_session_memory_returns_false_when_lock_not_acquired() -> None:
    redis_client = FakeRedis(lock_acquired=False)
    stm = stm_memory.RedisShortTermMemory(redis_client=redis_client)
    meta = SessionMeta(total_turns=8, last_compressed_turn=1)
    messages = [
        _build_message(
            message_label=f"msg-{index}",
            role="user" if index % 2 else "assistant",
            created_at=index,
        )
        for index in range(1, 11)
    ]

    async def fake_get_meta(*_args) -> SessionMeta:
        return meta

    async def fake_get_message_count(*_args) -> int:
        return len(messages)

    async def fake_get_summary(*_args):
        return None

    async def fake_get_recent_messages(*_args, **_kwargs) -> list[MessageRecord]:
        return messages

    async def fake_llm_compress(_summary: str, _messages: list[MessageRecord]) -> str:
        raise AssertionError("should not be called when lock is not acquired")

    stm.get_meta = fake_get_meta  # type: ignore[method-assign]
    stm.get_message_count = fake_get_message_count  # type: ignore[method-assign]
    stm.get_summary = fake_get_summary  # type: ignore[method-assign]
    stm.get_recent_messages = fake_get_recent_messages  # type: ignore[method-assign]

    compressed = _run(
        stm.compress_session_memory(
            "tenant-1",
            "user-1",
            "session-1",
            fake_llm_compress,
        )
    )

    keys = stm._build_session_keys("tenant-1", "user-1", "session-1")
    assert compressed is False
    assert redis_client.set_calls == [
        {
            "key": keys["lock"],
            "value": "1",
            "ex": stm.lock_ttl_seconds,
            "nx": True,
        }
    ]
    assert redis_client.deleted_keys == []
    assert redis_client.zadd_calls == []
