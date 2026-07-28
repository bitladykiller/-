"""LLM 模型入口与结构化输出模型。

职责：
- 统一创建 Agent 运行时使用的 DeepSeek / Ollama 模型
- 按逻辑角色维护温度配置，避免节点层分散写死参数
- 通过懒初始化代理避免 import 阶段就连接外部 LLM
- 存放节点会用到的结构化输出模型
- 模型缓存统一由 AppContainer 管理
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, TypeAlias

from app.chat.infrastructure.graph.state import (
    GuardrailsAction,
    ReactJudgeDecision,
    RetrievalComplexity,
    RetrievalMode,
    RoutingKind,
)
from app.shared.core.config import settings
from app.shared.core.config_models import ServiceType
from app.shared.core.logger import get_logger
from pydantic import BaseModel, Field

logger = get_logger(__name__)

ModelRole = Literal[
    "agent",
    "router",
    "guardrails",
    "cypher",
    "react",
    "react_judge",
    "memory_extractor",
]
ModelResolver: TypeAlias = Callable[[ModelRole, float], Any]

MODEL_TEMPERATURES: dict[ModelRole, float] = {
    "agent": 0.7,
    "router": 0.1,
    "guardrails": 0.1,
    "cypher": 0.2,
    "react": 0.4,
    "react_judge": 0.1,
    "memory_extractor": 0.3,
}

#: 按角色的请求超时（秒）。
#: WHY 分级：一次问答最多串 6+ 次 LLM 调用，此前没有任何超时——上游 API
#: 挂起时请求会无限等待。决策类角色（统一路由决策/守卫/裁判）输出只有几十
#: token，10s 等不到就该失败让降级逻辑接管；生成类角色（回答/摘要/抽取）
#: 要流式输出长文，给到 60s。
MODEL_TIMEOUTS_SECONDS: dict[ModelRole, float] = {
    "agent": 60.0,
    "router": 10.0,
    "guardrails": 10.0,
    "cypher": 20.0,
    "react": 60.0,
    "react_judge": 10.0,
    "memory_extractor": 60.0,
}
_DEFAULT_TIMEOUT_SECONDS = 60.0
#: 瞬时故障（连接抖动/限速）重试一次；避免更多次数放大上游过载
_MAX_RETRIES = 1


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

    def __getattr__(self, item: str) -> Any:
        # slots 属性读取不走 __getattr__；此处 item 仅用于转发模型方法
        return getattr(self._resolver(self._name, self._temperature), item)  # pyright: ignore[reportArgumentType]

    def __bool__(self) -> bool:
        return True

    def __await__(self):
        return self._resolver(self._name, self._temperature).__await__()  # pyright: ignore[reportArgumentType]

    def __str__(self) -> str:
        return f"_LazyModel(name={self._name}, t={self._temperature})"

    def __repr__(self) -> str:
        return self.__str__()


def _get_model(name: ModelRole, temperature: float) -> Any:
    """按逻辑角色从 AppContainer 缓存获取/创建模型实例。

    缓存键使用 agent/router/react... 这类角色名，调用方只关心
    "这个节点要什么温度和职责"，不必知道底层是 DeepSeek 还是 Ollama。

    注意：调用方必须在异步上下文中通过 await 访问 LazyModelProxy，
    或者确保没有运行中的事件循环（同步测试/脚本场景）。
    """
    import asyncio as _asyncio

    try:
        loop = _asyncio.get_running_loop()
    except RuntimeError:
        return _create_model(name, temperature)

    from app.platform.container import get_container

    async def _resolve():
        container = await get_container()
        if name not in container.llm_models:
            logger.info("初始化 LLM 模型 | name=%s | temperature=%s", name, temperature)
            container.llm_models[name] = _create_model(name, temperature)
        return container.llm_models[name]

    # 在已有事件循环但可能在协程内部被同步调用时，直接用 nest_asyncio
    # 兼容或通过 create_task 提交。但这里保持原有语义：如果循环已在运行
    # 但无法 run_until_complete，说明调用方在协程中同步访问了代理属性，
    # 应该用 await 代替直接属性访问。
    return loop.run_until_complete(_resolve())


def _create_model(name: ModelRole, temperature: float) -> Any:
    """直接创建模型实例（同步，不依赖容器）。

    统一挂接按角色的超时与瞬时重试策略（见 MODEL_TIMEOUTS_SECONDS）。
    """
    timeout = MODEL_TIMEOUTS_SECONDS.get(name, _DEFAULT_TIMEOUT_SECONDS)
    if settings.AGENT_SERVICE == ServiceType.DEEPSEEK:
        from langchain_deepseek import ChatDeepSeek

        return ChatDeepSeek(
            api_key=settings.DEEPSEEK_API_KEY,
            model_name=settings.DEEPSEEK_MODEL,  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
            temperature=temperature,
            timeout=timeout,
            max_retries=_MAX_RETRIES,
        )
    else:
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=settings.OLLAMA_AGENT_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=temperature,
            # Ollama 客户端经 httpx 请求本地服务，同样必须有超时护栏
            client_kwargs={"timeout": timeout},
        )


def create_llm_for_role(role: ModelRole) -> Any:
    """统一的 LLM 创建工厂。

    供 models.py 和 container.py 共用，消除重复的 ServiceType 判断逻辑。
    """
    temperature = MODEL_TEMPERATURES.get(role, 0.7)
    return _create_model(role, temperature)


# 模块级模型入口（懒加载代理）
agent_model = LazyModelProxy("agent", MODEL_TEMPERATURES["agent"], _get_model)
router_model = LazyModelProxy("router", MODEL_TEMPERATURES["router"], _get_model)
guardrails_model = LazyModelProxy("guardrails", MODEL_TEMPERATURES["guardrails"], _get_model)
cypher_model = LazyModelProxy("cypher", MODEL_TEMPERATURES["cypher"], _get_model)
react_model = LazyModelProxy("react", MODEL_TEMPERATURES["react"], _get_model)
react_judge_model = LazyModelProxy("react_judge", MODEL_TEMPERATURES["react_judge"], _get_model)


# ================================================================== #
# 节点输出模型 — 结构化输出定义
# ================================================================== #


class RoutingDecisionOutput(BaseModel):
    """统一路由决策的输出：类型 + 能力标签 + 编排，而非互斥五选一。

    执行路径由代码根据字段组合解析（见 resolve_execution_plan）。
    """

    logic: str = Field(description="选择该能力组合的理由")
    type: RoutingKind = Field(
        description="general=直接回复；rag_doc-query=需要进入 Guardrails 后执行知识检索"
    )
    need_graph: bool = Field(default=False, description="是否需要查询 Neo4j 结构化知识图谱")
    need_rag: bool = Field(default=False, description="是否需要查询文档 RAG 知识库")
    mode: RetrievalMode = Field(
        default="single",
        description=(
            "当 need_graph 与 need_rag 同时为 true 时："
            "parallel=并行查询；sequential=先图后文档；"
            "single=理论上只应一侧为 true，若两侧都 true 则按 parallel 处理"
        ),
    )
    complexity: RetrievalComplexity = Field(
        default="simple",
        description="simple=单跳可答；multi_hop=模糊/多跳/需动态探索 → 走 ReAct",
    )


class GuardrailsDecision(BaseModel):
    """Guardrails 节点的输出结构。"""

    decision: GuardrailsAction = Field(description="是否继续执行后续检索流程")


class ReactAnswerCheckOutput(BaseModel):
    """ReAct 答案校验器的输出结构。"""

    decision: ReactJudgeDecision = Field(description="当前答案是否足够，或需要继续检索/转人工")
    reason: str = Field(description="做出该判断的原因，供下一轮 ReAct 参考")


__all__ = [
    "GuardrailsDecision",
    "LazyModelProxy",
    "MODEL_TEMPERATURES",
    "ModelRole",
    "ReactAnswerCheckOutput",
    "RoutingDecisionOutput",
    "agent_model",
    "cypher_model",
    "guardrails_model",
    "react_judge_model",
    "react_model",
    "router_model",
    "create_llm_for_role",
]
