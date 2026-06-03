"""
LLM 模型工厂 + 温度分离实例。

v3.15: 从 lg_builder.py 拆分。集中管理所有 LLM 模型实例的创建和配置。
v3.15-hotfix: 改为懒初始化，避免 import 时连接 LLM 服务导致启动崩溃。

温度分配策略：
- 0.1 — 路由/守卫/裁判等需要确定性输出的结构化任务
- 0.2 — Cypher 生成（需要一定灵活性但不能太随机）
- 0.4 — ReAct Agent（需要探索能力）
- 0.7 — 通用聊天（需要创造性）
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import settings, ServiceType

logger = logging.getLogger(__name__)


def create_agent_model(temperature: float = 0.7):
    """根据 AGENT_SERVICE 配置创建 LLM 实例。

    Args:
        temperature: 采样温度。0.0 = 确定性，1.0 = 最大随机性。

    Returns:
        ChatDeepSeek 或 ChatOllama 实例。
    """
    if settings.AGENT_SERVICE == ServiceType.DEEPSEEK:
        from langchain_deepseek import ChatDeepSeek
        return ChatDeepSeek(
            api_key=settings.DEEPSEEK_API_KEY,
            model_name=settings.DEEPSEEK_MODEL,
            temperature=temperature,
        )
    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=settings.OLLAMA_AGENT_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=temperature,
    )


# ================================================================== #
# 懒初始化模型单例 — 首次访问时创建，避免 import 时连接 LLM 服务
# ================================================================== #

_models_cache: dict = {}


def _get_model(name: str, temperature: float):
    """懒初始化模型单例。首次调用时创建，后续返回缓存。"""
    if name not in _models_cache:
        logger.info("初始化 LLM 模型 | name=%s | temperature=%s", name, temperature)
        _models_cache[name] = create_agent_model(temperature)
    return _models_cache[name]


# 通用聊天 — 高温度，回复更自然
def _agent():
    return _get_model("agent", 0.7)

# 路由分类 — 低温度，分类结果稳定
def _router():
    return _get_model("router", 0.1)

# 检索计划路由 — 低温度，策略选择一致
def _retrieval_plan():
    return _get_model("retrieval_plan", 0.1)

# Guardrails 守卫 — 低温度，安全判断确定性强
def _guardrails():
    return _get_model("guardrails", 0.1)

# Text2Cypher 生成 — 中低温度，Cypher 语法需要准确但允许一定灵活性
def _cypher():
    return _get_model("cypher", 0.2)

# ReAct Agent — 中温度，需要探索能力但不能太发散
def _react():
    return _get_model("react", 0.4)

# ReAct 答案裁判 — 低温度，评判标准需要一致
def _react_judge():
    return _get_model("react_judge", 0.1)


# ================================================================== #
# 向后兼容的属性访问（模块级名称，实际懒初始化）
# ================================================================== #

class _LazyModel:
    """延迟代理：访问 .xxx() 时才真正创建模型。"""
    def __init__(self, name: str, temperature: float):
        self._name = name
        self._temperature = temperature

    def __getattr__(self, item):
        model = _get_model(self._name, self._temperature)
        return getattr(model, item)


agent_model = _LazyModel("agent", 0.7)
router_model = _LazyModel("router", 0.1)
retrieval_plan_model = _LazyModel("retrieval_plan", 0.1)
guardrails_model = _LazyModel("guardrails", 0.1)
cypher_model = _LazyModel("cypher", 0.2)
react_model = _LazyModel("react", 0.4)
react_judge_model = _LazyModel("react_judge", 0.1)
