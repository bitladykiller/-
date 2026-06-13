"""LLM 模型入口与结构化输出模型。

职责：
- 统一创建 Agent 运行时使用的 DeepSeek / Ollama 模型
- 按逻辑角色维护温度配置，避免节点层分散写死参数
- 通过懒初始化代理避免 import 阶段就连接外部 LLM
- 存放节点会用到的结构化输出模型
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logger import get_logger
from app.lg_agent.lg_model_support import (
    LazyModelProxy,
    MODEL_TEMPERATURES,
    ModelFactory,
    ModelRole,
    build_lazy_model,
    get_or_create_cached_model,
    lazy_model_repr,
    resolve_model_factory,
)
from app.lg_agent.lg_states import (
    RetrievalPlanType,
    GuardrailsAction,
    ReactJudgeDecision,
)

logger = get_logger(__name__)


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
    return resolve_model_factory(
        settings.AGENT_SERVICE,
        deepseek_factory=_create_deepseek_model,
        ollama_factory=_create_ollama_model,
    )


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


def clear_model_cache() -> None:
    """清空模型缓存。

    主要用于测试，以及后续如果需要在不重启进程的情况下切换配置时手动刷新。
    """
    _models_cache.clear()


def _get_model(name: ModelRole, temperature: float) -> Any:
    """按逻辑角色缓存模型实例。

    缓存键使用 `agent/router/react...` 这类角色名，而不是 provider 名称。
    这样调用方只关心“这个节点要什么温度和职责”，不必知道底层是
    DeepSeek 还是 Ollama。
    """
    return get_or_create_cached_model(
        _models_cache,
        name,
        lambda: _create_logged_model(name, temperature),
    )


def _create_logged_model(name: ModelRole, temperature: float) -> Any:
    """创建模型前统一记录初始化日志。"""
    logger.info("初始化 LLM 模型 | name=%s | temperature=%s", name, temperature)
    return _create_chat_model(temperature)


# ================================================================== #
# 模块级模型入口（实际使用的是这些懒加载代理）
# ================================================================== #

_LazyModel = LazyModelProxy


def _lazy_model_repr(name: ModelRole, temperature: float) -> str:
    """构造懒代理的稳定字符串表示。"""
    return lazy_model_repr(name, temperature)


def _lazy_model(name: ModelRole) -> _LazyModel:
    """按角色名创建懒加载代理，统一温度来源。"""
    return build_lazy_model(name, _get_model)


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
