"""对话域 Agent 通用工具函数。

职责：
- 提供跨节点共享的小型纯函数
- 避免把问题提取、统一降级响应散落到多个节点文件

注意：
- 位于 infrastructure/utils/ 而非 domain/，
  因为 question_from_state 依赖 AgentState（infrastructure/graph/state.py），
  domain 层不应反向依赖 infrastructure 层。
- 本包不是全局 app.shared；业务域禁止再命名 shared。
"""

from app.chat.infrastructure.graph.state import AgentState
from langchain_core.messages import AIMessage


def question_from_state(state: AgentState) -> str:
    """取 messages 最后一条的可展示文本（兼容 str / 多模态 content 列表）。"""
    if not state.messages:
        return ""
    content = state.messages[-1].content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
        return "".join(parts)
    return str(content)


def no_neo4j_response() -> dict[str, object]:
    """KG 不可用时的统一用户可见降级（与 RAG 不可用文案区分）。"""
    return {
        "messages": [
            AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")
        ]
    }


__all__ = [
    "no_neo4j_response",
    "question_from_state",
]
