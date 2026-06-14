"""Agent 通用工具函数。

职责：
- 提供跨节点共享的小型纯函数
- 避免把问题提取、统一降级响应散落到多个节点文件
"""

from langchain_core.messages import AIMessage

from app.chat.infrastructure.graph.state import AgentState


def question_from_state(state: AgentState) -> str:
    """从 AgentState 中提取最新一条用户问题。"""
    return state.messages[-1].content if state.messages else ""


def no_neo4j_response() -> dict:
    """Neo4j 不可用时的统一降级响应。"""
    return {
        "messages": [
            AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")
        ]
    }


__all__ = [
    "no_neo4j_response",
    "question_from_state",
]
