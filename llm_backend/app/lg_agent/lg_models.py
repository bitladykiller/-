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
    """延迟代理：访问属性/方法时才真正创建模型。

    v3.17 修复：补充 __bool__ / __await__ / __str__ / __repr__，
    防止 `if model:` 恒为 True 误导、`await model` 触发 AttributeError。
    """
    def __init__(self, name: str, temperature: float):
        self._name = name
        self._temperature = temperature

    def _get(self):
        """返回底层模型实例。"""
        return _get_model(self._name, self._temperature)

    def __getattr__(self, item):
        return getattr(self._get(), item)

    def __bool__(self) -> bool:
        """总是返回 True — 懒加载代理总是"可用"。"""
        return True

    def __await__(self):
        """支持 `await lazy_model` — 代理到底层模型的 __await__。"""
        return self._get().__await__()

    def __str__(self) -> str:
        return f"_LazyModel(name={self._name}, t={self._temperature})"

    def __repr__(self) -> str:
        return self.__str__()


agent_model = _LazyModel("agent", 0.7)
router_model = _LazyModel("router", 0.1)
retrieval_plan_model = _LazyModel("retrieval_plan", 0.1)
guardrails_model = _LazyModel("guardrails", 0.1)
cypher_model = _LazyModel("cypher", 0.2)
react_model = _LazyModel("react", 0.4)
react_judge_model = _LazyModel("react_judge", 0.1)


# ================================================================== #
# 节点输出模型 — 结构化输出定义
# ================================================================== #
#
# v3.17: 从 lg_nodes.py 迁移至此。节点函数和模型类职责不同，
# 模型类放在 lg_models.py 更符合模块边界。
# ================================================================== #

from pydantic import BaseModel, Field
from typing import Literal


class RetrievalPlanOutput(BaseModel):
    """检索计划路由器的输出结构。"""
    logic: str = Field(description="选择该计划的理由")
    plan: Literal["GRAPH_ONLY", "RAG_ONLY", "PARALLEL", "GRAPH_THEN_RAG", "AGENT_REACT"] = Field(
        description="最合适的检索策略"
    )


class ReactAnswerCheckOutput(BaseModel):
    """ReAct 答案校验器的输出结构。"""
    decision: Literal["sufficient", "retry", "handoff"] = Field(
        description="当前答案是否足够，或需要继续检索/转人工"
    )
    reason: str = Field(description="做出该判断的原因，供下一轮 ReAct 参考")
