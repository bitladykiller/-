"""对话域 domain 层：契约与纯规则边界。

图运行时状态（AgentState）留在 infrastructure/graph/state，
避免 domain 依赖 LangGraph / LangChain 运行时。
"""
