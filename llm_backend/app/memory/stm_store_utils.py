"""短期记忆存储层共享 helper。

这个模块负责：
- 生成 session 级 Redis key
- 解析 Redis 中保存的 JSON / 压缩消息 payload
- 提供消息窗口切分、摘要 JSON 提取等纯数据处理函数

这个模块不负责：
- Redis 网络 I/O
- LLM 摘要压缩调用
- TTL、锁和滑动窗口的编排
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, TypedDict, TypeVar

from pydantic import BaseModel

from app.memory.schemas import MessageRecord, SessionSummary
from app.memory.stm_compressor import decompress_message

RedisModel = TypeVar("RedisModel", bound=BaseModel)


class SessionKeys(TypedDict):
    """单个 session 会使用到的全部 Redis key。"""

    messages: str
    summary: str
    meta: str
    lock: str


def build_session_key(
    key_prefix: str,
    tenant_id: str,
    user_id: str,
    session_id: str,
    suffix: str,
) -> str:
    """统一构造某个 session 下的 Redis key。"""
    return f"{key_prefix}:{tenant_id}:{user_id}:{session_id}:{suffix}"


def build_session_keys(
    key_prefix: str,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> SessionKeys:
    """一次性返回当前 session 会用到的所有 key。"""
    return {
        "messages": build_session_key(
            key_prefix,
            tenant_id,
            user_id,
            session_id,
            "messages",
        ),
        "summary": build_session_key(
            key_prefix,
            tenant_id,
            user_id,
            session_id,
            "summary",
        ),
        "meta": build_session_key(
            key_prefix,
            tenant_id,
            user_id,
            session_id,
            "meta",
        ),
        "lock": build_session_key(
            key_prefix,
            tenant_id,
            user_id,
            session_id,
            "lock",
        ),
    }


def decode_json_payload(raw: bytes | str | None) -> Any | None:
    """解码 Redis 中保存的 JSON 文本。"""
    if not raw:
        return None
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    return json.loads(text)


def decode_model(
    raw: bytes | str | None,
    model_cls: type[RedisModel],
) -> RedisModel | None:
    """把 Redis JSON 文本解码成指定 Pydantic 模型。"""
    data = decode_json_payload(raw)
    if not isinstance(data, dict):
        return None
    return model_cls(**data)


def message_score(message: MessageRecord) -> int:
    """把消息时间统一转换成 Redis ZSET 所需的毫秒 score。"""
    if message.created_at > 1000000000000:
        return message.created_at
    return int(time.time() * 1000)


def extract_summary_from_response(response: str) -> SessionSummary | None:
    """从 LLM 压缩结果中提取 SessionSummary JSON。"""
    match = re.search(r"\{.*\}", response, re.DOTALL)
    if not match:
        return None
    data = json.loads(match.group())
    return SessionSummary(**data)


def decode_messages(raw_messages: list[bytes | str]) -> list[MessageRecord]:
    """批量解压 Redis 中保存的消息记录。"""
    messages: list[MessageRecord] = []
    for raw_message in raw_messages:
        try:
            messages.append(decompress_message(raw_message))
        except Exception:
            continue

    messages.reverse()
    return messages


def split_messages_for_compression(
    messages: list[MessageRecord],
    keep_recent_rounds: int,
) -> tuple[list[MessageRecord], list[MessageRecord]]:
    """把消息切成“需要压缩的旧消息”和“需要保留的最近消息”。

    当前默认一轮对话按 user + assistant 两条消息估算，因此
    `keep_recent_rounds` 会换算成 `keep_recent_rounds * 2` 条消息。
    """
    recent_count = keep_recent_rounds * 2
    messages_to_keep = messages[-recent_count:]
    messages_to_compress = messages[:-recent_count] if len(messages) > recent_count else []
    return messages_to_compress, messages_to_keep
