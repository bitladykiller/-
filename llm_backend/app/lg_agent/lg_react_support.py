"""`lg_react.py` 共享的纯 helper。

这个模块负责：
- ReAct 工具输出与主图回复的统一格式化
- transcript 截断、重试提示和裁判输入消息拼装
- “步数耗尽”与最终答案提取这类轻量判断

这个模块不负责：
- 调用 ReAct 子图
- 调用裁判模型
- 初始化运行时单例
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage

REACT_TRANSCRIPT_WINDOW = 20
REACT_PROGRESS_MESSAGE = "正在综合分析..."
REACT_FALLBACK_ANSWER = "亲～这个问题回答不了哦～"
REACT_RETRY_PROMPT = (
    "上一次候选答案仍然不充分，请继续按标准 ReAct 检索并补足关键事实。"
)
REACT_STEP_EXHAUSTED_MARKER = "need more steps"
REACT_STEP_EXHAUSTED_REASON = "单次 ReAct 内部步数耗尽，仍未得到足够答案。"
REACT_DEFAULT_INSUFFICIENCY_REASON = "答案信息不足。"
REACT_INITIAL_REASON = "初始状态：尚未完成充分回答。"


def dump_retriever_records(result: dict[str, Any]) -> str:
    """将统一 Retriever 结果中的 `records` 序列化为工具输出。"""
    return json.dumps(result.get("records", []), ensure_ascii=False)


def build_tool_error(message: str) -> str:
    """统一构造 ReAct 工具层的降级响应。"""
    return json.dumps({"error": message}, ensure_ascii=False)


def build_react_response(answer: str) -> dict[str, list[AIMessage]]:
    """统一构造 ReAct 节点返回给主图的两段式消息。"""
    return {
        "messages": [
            AIMessage(content=REACT_PROGRESS_MESSAGE),
            AIMessage(content=answer),
        ],
    }


def extract_last_answer(result_messages: list[Any]) -> str:
    """提取 ReAct 子图返回的最后一条答案文本。"""
    if not result_messages:
        return "未能确定回答～"

    last_content = getattr(result_messages[-1], "content", "")
    return str(last_content) if last_content else "未能确定回答～"


def build_transcript(result_messages: list[Any]) -> str:
    """截断并格式化最近的 ReAct 过程，供裁判模型判断答案充分性。"""
    transcript_lines: list[str] = []
    for message in result_messages[-REACT_TRANSCRIPT_WINDOW:]:
        role = getattr(message, "type", None) or getattr(message, "role", "assistant")
        content = getattr(message, "content", "")
        if content:
            transcript_lines.append(f"[{role}] {content}")
    return "\n".join(transcript_lines)


def build_retry_message(reason: str) -> dict[str, str]:
    """构造下一轮 ReAct 的补检提示。"""
    return {
        "role": "user",
        "content": f"{REACT_RETRY_PROMPT}不足原因：{reason}",
    }


def build_answer_check_messages(
    *,
    judge_system_prompt: str,
    question: str,
    transcript: str,
    candidate_answer: str,
) -> list[dict[str, str]]:
    """构造 ReAct 裁判模型的输入消息。"""
    return [
        {"role": "system", "content": judge_system_prompt},
        {
            "role": "user",
            "content": (
                f"用户问题：{question}\n\n"
                f"ReAct 过程记录：\n{transcript}\n\n"
                f"当前候选答案：{candidate_answer}"
            ),
        },
    ]


def build_retry_seed_messages(
    question: str,
    candidate_answer: str,
) -> list[dict[str, str]]:
    """保留原问题和上一轮候选答案，作为下一轮 ReAct 的起点。"""
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": candidate_answer},
    ]


def needs_more_steps(answer: str) -> bool:
    """判断 ReAct 是否因为内部步数用尽而终止。"""
    return REACT_STEP_EXHAUSTED_MARKER in answer.lower()
