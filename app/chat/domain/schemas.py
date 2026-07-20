"""对话域领域契约说明。

本阶段最小 domain：明确「什么属于对话域契约」与「什么仍在 infrastructure」。

属于 domain 边界（概念上）：
- 会话元信息语义（标题、类型、归属用户）
- 问答请求/响应的业务字段含义

明确仍在 infrastructure 的原因：
- AgentState / Router / RetrievalPlan：依赖 LangGraph 消息类型与图执行
- LLM 代理与 Prompt 组装：依赖具体模型 SDK
- Neo4j / Retriever 实现：技术适配器

后续若抽出与运行时无关的 TypedDict/Pydantic，优先放本模块。
"""

from __future__ import annotations

# 占位：保持包可导入；具体类型按需从 modeling/graph 下沉。
__all__: list[str] = []
