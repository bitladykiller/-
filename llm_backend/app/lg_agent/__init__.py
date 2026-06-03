"""
LangGraph Agent Package。

架构 v3.16:
  lg_models.py    — LLM 模型工厂 + 温度分离实例（懒初始化）
  lg_states.py    — Agent 状态定义（InputState / AgentState）
  lg_prompts.py   — 所有 Prompt 模板
  lg_context.py   — 记忆中间件 + 记忆上下文构建（含优先级模型）
  lg_retrievers.py — Retriever 接口抽象 + 具体实现（依赖倒置）
  lg_nodes.py     — 所有图节点函数
  lg_builder.py   — 图组装（StateGraph compile）
"""