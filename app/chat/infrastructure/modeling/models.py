"""LLM 模型入口与结构化输出模型。

职责：
- 统一创建 Agent 运行时使用的 DeepSeek / Ollama 模型
- 按逻辑角色维护温度配置，避免节点层分散写死参数
- 通过懒初始化代理避免 import 阶段就连接外部 LLM
- 存放节点会用到的结构化输出模型
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field

from app.shared.core.config import settings
from app.shared.core.logger import get_logger
from app.chat.infrastructure.graph.state import (
    RetrievalPlanType,
    GuardrailsAction,
    ReactJudgeDecision,
)

logger = get_logger(__name__)

ModelRole = Literal[
    "agent",
    "router",
    "retrieval_plan",
    "guardrails",
    "cypher",
    "react",
    "react_judge",
]
ModelFactory: TypeAlias = Callable[[float], Any]
ModelResolver: TypeAlias = Callable[[ModelRole, float], Any]

MODEL_TEMPERATURES: dict[ModelRole, float] = {
    "agent": 0.7,
    "router": 0.1,
    "retrieval_plan": 0.1,
    "guardrails": 0.1,
    "cypher": 0.2,
    "react": 0.4,
    "react_judge": 0.1,
}


class LazyModelProxy:
    """延迟代理：访问属性/方法时才真正创建模型。"""

    __slots__ = ("_name", "_temperature", "_resolver")

    def __init__(
        self,
        name: ModelRole,
        temperature: float,
        resolver: ModelResolver,
    ) -> None:
        self._name = name
        self._temperature = temperature
        self._resolver = resolver

    def _get(self) -> Any:
        """返回底层模型实例。"""
        return self._resolver(self._name, self._temperature)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._get(), item)

    def __bool__(self) -> bool:
        """总是返回 True，避免代理在条件判断里表现反常。"""
        return True

    def __await__(self):
        """支持 `await lazy_model`，直接代理到底层模型。"""
        return self._get().__await__()

    def __str__(self) -> str:
        return f"_LazyModel(name={self._name}, t={self._temperature})"

    def __repr__(self) -> str:
        return self.__str__()


def _create_deepseek_model(temperature: float) -> Any:
    """创建 DeepSeek ChatModel。"""
    from langchain_deepseek import ChatDeepSeek

    return ChatDeepSeek(
        api_key=settings.DEEPSEEK_API_KEY,
        model_name=settings.DEEPSEEK_MODEL,
        temperature=temperature,
    )


def _create_ollama_model(temperature: float) -> Any:
    """创建 Ollama ChatModel。"""
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=settings.OLLAMA_AGENT_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=temperature,
    )


def _resolve_model_factory() -> ModelFactory:
    """根据 `AGENT_SERVICE` 选择当前运行时要用的模型工厂。"""
    if settings.AGENT_SERVICE == "deepseek":
        return _create_deepseek_model
    return _create_ollama_model


def _create_chat_model(temperature: float = MODEL_TEMPERATURES["agent"]) -> Any:
    """根据 AGENT_SERVICE 配置创建运行时 LLM 实例。

    Args:
        temperature: 采样温度。0.0 = 确定性，1.0 = 最大随机性。

    Returns:
        ChatDeepSeek 或 ChatOllama 实例。
    """
    return _resolve_model_factory()(temperature)


# ================================================================== #
# 懒初始化模型单例 — 首次访问时创建，避免 import 时连接 LLM 服务
# ================================================================== #

_models_cache: dict[ModelRole, Any] = {}


def _get_model(name: ModelRole, temperature: float) -> Any:
    """按逻辑角色缓存模型实例。

    缓存键使用 `agent/router/react...` 这类角色名，而不是 provider 名称。
    这样调用方只关心“这个节点要什么温度和职责”，不必知道底层是
    DeepSeek 还是 Ollama。
    """
    if name not in _models_cache:
        _models_cache[name] = _create_logged_model(name, temperature)
    return _models_cache[name]


def _create_logged_model(name: ModelRole, temperature: float) -> Any:
    """创建模型前统一记录初始化日志。"""
    logger.info("初始化 LLM 模型 | name=%s | temperature=%s", name, temperature)
    return _create_chat_model(temperature)


# ================================================================== #
# 模块级模型入口（实际使用的是这些懒加载代理）
# ================================================================== #

def _lazy_model(name: ModelRole) -> LazyModelProxy:
    """按角色名创建懒加载代理，统一温度来源。"""
    return LazyModelProxy(name, MODEL_TEMPERATURES[name], _get_model)


agent_model = _lazy_model("agent")
router_model = _lazy_model("router")
retrieval_plan_model = _lazy_model("retrieval_plan")
guardrails_model = _lazy_model("guardrails")
cypher_model = _lazy_model("cypher")
react_model = _lazy_model("react")
react_judge_model = _lazy_model("react_judge")


# ================================================================== #
# 节点输出模型 — 结构化输出定义
# ================================================================== #
#
# 这些模型和节点函数职责不同：节点负责流程编排，模型负责约束结构化输出。
# 放在同一个“模型入口”文件里，调用方更容易定位。
# ================================================================== #


class RetrievalPlanOutput(BaseModel):
    """检索计划路由器的输出结构。"""
    logic: str = Field(description="选择该计划的理由")
    plan: RetrievalPlanType = Field(
        description="最合适的检索策略"
    )


class GuardrailsDecision(BaseModel):
    """Guardrails 节点的输出结构。"""
    decision: GuardrailsAction = Field(description="是否继续执行后续检索流程")


class ReactAnswerCheckOutput(BaseModel):
    """ReAct 答案校验器的输出结构。"""
    decision: ReactJudgeDecision = Field(
        description="当前答案是否足够，或需要继续检索/转人工"
    )
    reason: str = Field(description="做出该判断的原因，供下一轮 ReAct 参考")


__all__ = [
    "GuardrailsDecision",
    "LazyModelProxy",
    "MODEL_TEMPERATURES",
    "ModelFactory",
    "ModelRole",
    "ReactAnswerCheckOutput",
    "RetrievalPlanOutput",
    "agent_model",
    "cypher_model",
    "guardrails_model",
    "react_judge_model",
    "react_model",
    "retrieval_plan_model",
    "router_model",
]
