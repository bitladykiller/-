"""
LLM 模型工厂 + 温度分离实例。

v3.15: 从 lg_builder.py 拆分。集中管理所有 LLM 模型实例的创建和配置。

温度分配策略：
- 0.1 — 路由/守卫/裁判等需要确定性输出的结构化任务
- 0.2 — Cypher 生成（需要一定灵活性但不能太随机）
- 0.4 — ReAct Agent（需要探索能力）
- 0.7 — 通用聊天（需要创造性）
"""
from __future__ import annotations

from langchain_deepseek import ChatDeepSeek
from langchain_ollama import ChatOllama

from app.core.config import settings, ServiceType


def create_agent_model(temperature: float = 0.7):
    """根据 AGENT_SERVICE 配置创建 LLM 实例。

    Args:
        temperature: 采样温度。0.0 = 确定性，1.0 = 最大随机性。

    Returns:
        ChatDeepSeek 或 ChatOllama 实例。
    """
    if settings.AGENT_SERVICE == ServiceType.DEEPSEEK:
        return ChatDeepSeek(
            api_key=settings.DEEPSEEK_API_KEY,
            model_name=settings.DEEPSEEK_MODEL,
            temperature=temperature,
        )
    return ChatOllama(
        model=settings.OLLAMA_AGENT_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=temperature,
    )


# ================================================================== #
# 温度分离的模型单例 — 每个职责一个独立实例
# ================================================================== #

# 通用聊天 — 高温度，回复更自然
agent_model = create_agent_model(0.7)

# 路由分类 — 低温度，分类结果稳定
router_model = create_agent_model(0.1)

# 检索计划路由 — 低温度，策略选择一致
retrieval_plan_model = create_agent_model(0.1)

# Guardrails 守卫 — 低温度，安全判断确定性强
guardrails_model = create_agent_model(0.1)

# Text2Cypher 生成 — 中低温度，Cypher 语法需要准确但允许一定灵活性
cypher_model = create_agent_model(0.2)

# ReAct Agent — 中温度，需要探索能力但不能太发散
react_model = create_agent_model(0.4)

# ReAct 答案裁判 — 低温度，评判标准需要一致
react_judge_model = create_agent_model(0.1)
