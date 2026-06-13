"""长期记忆抽取层的纯 helper。

这个模块负责：
- 解析 LLM 返回文本中的 JSON
- 对长期记忆候选内容做脱敏与可保存性过滤
- 把语义记忆候选项转换为强类型结果

这个模块不负责：
- 调用 LLM
- 构造抽取 prompt
- 处理用户画像持久化
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.memory.config import long_term_memory_type_values
from app.memory.schemas import MemoryExtractorResult, SessionSummary

_SAVEABLE_MEMORY_TYPES = long_term_memory_type_values()
_GREETING_MESSAGES = frozenset(
    {
        "你好",
        "谢谢",
        "不客气",
        "再见",
        "好的",
        "嗯",
        "哦",
        "哈哈",
        "呵呵",
    }
)
_PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
_ID_CARD_PATTERN = re.compile(r"\d{17}[\dXx]")
_BANK_CARD_PATTERN = re.compile(r"\d{16,19}")
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _mask_phone_match(match: re.Match[str]) -> str:
    """手机号保留前三后四，中间四位打码。"""
    value = match.group()
    return value[:3] + "****" + value[-4:]


def _mask_id_card_match(match: re.Match[str]) -> str:
    """身份证号保留前四后四，中间打码。"""
    value = match.group()
    return value[:4] + "**********" + value[-4:]


def _mask_bank_card_match(match: re.Match[str]) -> str:
    """银行卡号保留前四后四，按分段形式打码。"""
    value = match.group()
    return value[:4] + " **** **** " + value[-4:]


def _mask_email_match(match: re.Match[str]) -> str:
    """邮箱保留前三位和域名，其余局部打码。"""
    value = match.group()
    local_part, _, domain = value.partition("@")
    return local_part[:3] + "***@" + domain


def should_save_memory_content(
    content: str,
    sensitive_patterns: tuple[re.Pattern[str], ...],
) -> bool:
    """判断抽取内容是否值得保存为长期记忆。"""
    if len(content) < 10:
        return False
    for pattern in sensitive_patterns:
        if pattern.search(content):
            return False
    if content.strip() in _GREETING_MESSAGES:
        return False
    return True


def mask_sensitive_info(content: str) -> str:
    """对可保留内容做脱敏，而不是简单丢弃整条信息。"""
    content = _PHONE_PATTERN.sub(_mask_phone_match, content)
    content = _ID_CARD_PATTERN.sub(_mask_id_card_match, content)
    content = _BANK_CARD_PATTERN.sub(_mask_bank_card_match, content)
    content = _EMAIL_PATTERN.sub(_mask_email_match, content)
    return content


def extract_summary_text(session_summary: str | SessionSummary | None) -> str:
    """把摘要对象统一转换为纯文本，避免把模型对象 repr 塞进 prompt。"""
    if isinstance(session_summary, SessionSummary):
        return session_summary.content.strip()
    if isinstance(session_summary, str):
        return session_summary.strip()
    return ""


def extract_response_text(response: Any) -> str:
    """兼容字符串、AIMessage.content 列表等不同 LLM 返回形态。"""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            part = extract_content_part_text(item)
            if part:
                text_parts.append(part)
        return "\n".join(text_parts)
    return str(content)


def extract_content_part_text(item: Any) -> str:
    """提取 content 列表中单个片段的文本内容。"""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        text = item.get("text")
        return text if isinstance(text, str) else ""

    text = getattr(item, "text", None)
    return text if isinstance(text, str) else ""


def extract_first_json_object(response: str) -> str | None:
    """提取首个完整 JSON 对象，避免贪婪正则吃掉额外文本。"""
    start = response.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(response)):
        char = response[index]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return response[start:index + 1]

    return None


def parse_llm_response(response: str) -> dict[str, Any]:
    """从 LLM 返回文本中提取首个 JSON 对象。"""
    try:
        payload = extract_first_json_object(response)
        if payload is None:
            return {}
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except Exception as exc:
        logger.debug(f"[memory] JSON 解析失败: {exc}")
        return {}


def build_semantic_memories(
    parsed: dict[str, Any],
    *,
    sensitive_patterns: tuple[re.Pattern[str], ...],
) -> list[MemoryExtractorResult]:
    """把 LLM 输出中的 semantic 数组转换为强类型结果。"""
    semantic_memories: list[MemoryExtractorResult] = []
    for item in parsed.get("semantic", []):
        if not isinstance(item, dict):
            continue

        content = mask_sensitive_info(item.get("content", ""))
        memory_type = item.get("memory_type", "")
        if not content or not should_save_memory_content(content, sensitive_patterns):
            continue
        if memory_type not in _SAVEABLE_MEMORY_TYPES:
            continue

        semantic_memories.append(
            MemoryExtractorResult(
                memory_type=memory_type,
                content=content,
                reason=item.get("reason"),
            )
        )
    return semantic_memories
