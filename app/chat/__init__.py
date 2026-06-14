"""Chat 域包入口。

职责：
- Agent 图编排
- 检索器
- ReAct 执行
- 记忆桥接
- 会话管理

边界：
- 图细节留在 infrastructure/graph/
- 检索细节留在 infrastructure/retrievers/
- ReAct 细节留在 infrastructure/react/
"""
