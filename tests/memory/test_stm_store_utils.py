from app.memory.schemas import MessageRecord, SessionMeta, SessionSummary
from app.memory.stm_compressor import compress_message
from app.memory.stm_store_utils import (
    build_session_keys,
    decode_messages,
    decode_model,
    extract_summary_from_response,
    message_score,
    split_messages_for_compression,
)


def _build_message(*, message_id: str, created_at: int, turn_index: int) -> MessageRecord:
    return MessageRecord(
        message_id=message_id,
        role="user" if turn_index % 2 else "assistant",
        content=f"content-{message_id}",
        created_at=created_at,
        turn_index=turn_index,
    )


def test_build_session_keys_uses_consistent_suffixes() -> None:
    keys = build_session_keys("stm", "tenant-1", "user-1", "session-1")

    assert keys == {
        "messages": "stm:tenant-1:user-1:session-1:messages",
        "summary": "stm:tenant-1:user-1:session-1:summary",
        "meta": "stm:tenant-1:user-1:session-1:meta",
        "lock": "stm:tenant-1:user-1:session-1:lock",
    }


def test_decode_model_accepts_bytes_and_ignores_non_dict_payload() -> None:
    meta = SessionMeta(total_turns=3, last_updated_at=10, last_compressed_turn=1)

    decoded = decode_model(meta.model_dump_json().encode("utf-8"), SessionMeta)

    assert decoded == meta
    assert decode_model('["not", "a", "dict"]', SessionMeta) is None
    assert decode_model(None, SessionMeta) is None


def test_decode_messages_skips_invalid_payload_and_restores_time_order() -> None:
    old_message = _build_message(message_id="msg-1", created_at=1, turn_index=1)
    new_message = _build_message(message_id="msg-2", created_at=2, turn_index=2)

    decoded = decode_messages(
        [
            compress_message(new_message),
            b"broken-payload",
            compress_message(old_message),
        ]
    )

    assert decoded == [old_message, new_message]


def test_split_messages_for_compression_keeps_recent_rounds() -> None:
    messages = [
        _build_message(message_id=f"msg-{index}", created_at=index, turn_index=index)
        for index in range(1, 7)
    ]

    messages_to_compress, messages_to_keep = split_messages_for_compression(
        messages,
        keep_recent_rounds=2,
    )

    assert [message.message_id for message in messages_to_compress] == ["msg-1", "msg-2"]
    assert [message.message_id for message in messages_to_keep] == [
        "msg-3",
        "msg-4",
        "msg-5",
        "msg-6",
    ]


def test_extract_summary_from_response_reads_json_block() -> None:
    summary = extract_summary_from_response(
        '前置说明 {"content":"压缩后摘要","compressed_at":12,"compressed_round":4} 尾部说明'
    )

    assert summary == SessionSummary(
        content="压缩后摘要",
        compressed_at=12,
        compressed_round=4,
    )


def test_message_score_uses_existing_millisecond_timestamp() -> None:
    message = _build_message(
        message_id="msg-1",
        created_at=1_700_000_000_123,
        turn_index=1,
    )

    assert message_score(message) == 1_700_000_000_123


def test_message_score_falls_back_to_current_time_for_second_timestamp(
    monkeypatch,
) -> None:
    message = _build_message(message_id="msg-1", created_at=1_700_000_000, turn_index=1)
    monkeypatch.setattr("app.memory.stm_store_utils.time.time", lambda: 123.456)

    assert message_score(message) == 123456
