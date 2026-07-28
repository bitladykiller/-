"""主图中的决策类节点实现。

这个模块负责：
- 统一的路由与检索规划节点
- general 回复节点
- guardrails 节点
- 已解析执行计划的边路由

这个模块不负责：
- KG / RAG 检索执行
- after_response 写回记忆
- 主图结构组装
"""

from __future__ import annotations

from typing import Literal

from app.chat.infrastructure.graph.execution_utils import (
    ainvoke_structured_question_output,
)
from app.chat.infrastructure.graph.memory_context import (
    build_memory_context,
    load_memory_state,
)
from app.chat.infrastructure.graph.message_utils import (
    build_safe_messages,
)
from app.chat.infrastructure.graph.state import (
    AgentState,
    ExecutionPlanType,
    RetrievalComplexity,
    RetrievalMode,
    RoutingDecision,
    RoutingKind,
)
from app.chat.infrastructure.modeling.models import (
    GuardrailsDecision,
    RoutingDecisionOutput,
    agent_model,
    guardrails_model,
    router_model,
)
from app.chat.infrastructure.modeling.prompts import (
    GENERAL_QUERY_SYSTEM_PROMPT,
    GUARDRAILS_SYSTEM_PROMPT,
    ROUTING_DECISION_PROMPT,
)
from app.chat.infrastructure.utils.helpers import question_from_state
from app.shared.security import wrap_user_message
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

RoutingDecisionEdgeName = Literal["respond_to_general_query", "guardrails_node"]
RetrievalEdgeName = Literal[
    "execute_graph_only",
    "execute_rag_only",
    "execute_parallel",
    "execute_then",
    "execute_react",
]
GuardrailsEdgeName = RetrievalEdgeName | Literal["after_response"]
_GUARDRAILS_BLOCK_MESSAGE = "抱歉，我家暂时没有这方面的商品，可以在别家看看哦～"
_RETRIEVAL_EDGE_MAP: dict[ExecutionPlanType, RetrievalEdgeName] = {
    "GRAPH_ONLY": "execute_graph_only",
    "RAG_ONLY": "execute_rag_only",
    "PARALLEL": "execute_parallel",
    "GRAPH_THEN_RAG": "execute_then",
    "AGENT_REACT": "execute_react",
}


def resolve_execution_plan(
    *,
    need_graph: bool,
    need_rag: bool,
    mode: RetrievalMode | str,
    complexity: RetrievalComplexity | str,
) -> ExecutionPlanType:
    """将能力标签解析为执行层路径（五类 execute 节点之一）。

    规则：
    - multi_hop → AGENT_REACT
    - 只要 graph → GRAPH_ONLY
    - 只要 rag → RAG_ONLY
    - 两侧都要 + sequential → GRAPH_THEN_RAG
    - 两侧都要 + 其它 → PARALLEL
    - 两侧都不要 → AGENT_REACT（安全兜底）
    """
    if complexity == "multi_hop":
        return "AGENT_REACT"
    if need_graph and need_rag:
        if mode == "sequential":
            return "GRAPH_THEN_RAG"
        return "PARALLEL"
    if need_graph:
        return "GRAPH_ONLY"
    if need_rag:
        return "RAG_ONLY"
    return "AGENT_REACT"


def _normalize_retrieval_mode(raw: object) -> RetrievalMode:
    """归一化**检索**模式（single / parallel / sequential）。

    注意与 `indexing_service.normalize_upload_mode` 区分：那个是文档上传模式
    （create / replace），两者语义完全不同。此前二者都叫 `_normalize_mode`，
    跨文件搜索时极易看串。
    """
    if raw in ("single", "parallel", "sequential"):
        return raw  # pyright: ignore[reportReturnType]
    return "single"


def _normalize_complexity(raw: object) -> RetrievalComplexity:
    if raw in ("simple", "multi_hop"):
        return raw  # pyright: ignore[reportReturnType]
    return "simple"


def _normalize_routing_kind(raw: object) -> RoutingKind:
    """归一化顶层路由类型，结构化输出异常时保守走 general。"""
    if raw in ("general", "rag_doc-query"):
        return raw  # pyright: ignore[reportReturnType]
    return "general"


SCOPE_DESCRIPTION = """
个人电商经营范围：智能家居产品（智能照明/安防/控制/音箱/厨电/清洁）。
不包含：服装、鞋类、体育用品、化妆品、食品等。
"""


async def build_general_query_system_prompt(
    *,
    state: AgentState,
    config: RunnableConfig,
    general_query_system_prompt: str,
) -> str:
    """构造 general 节点的系统提示词，并按需注入记忆上下文。"""
    system_prompt = general_query_system_prompt.format(logic=state.routing_decision["logic"])
    user_message = question_from_state(state)
    memory_state = await load_memory_state(state, config, user_message)
    if memory_state is None:
        return system_prompt

    memory_context = build_memory_context(
        memory_state.session_summary,
        memory_state.recent_messages,
        memory_state.long_term_memories,
        memory_state.user_profile,
    )
    if not memory_context:
        return system_prompt
    return system_prompt + memory_context


def _build_routing_decision(output: RoutingDecisionOutput) -> RoutingDecision:
    """把统一模型输出规整为状态，并由代码解析实际执行路径。"""
    route_type = _normalize_routing_kind(getattr(output, "type", "general"))
    logic = str(getattr(output, "logic", "") or "")
    if route_type == "general":
        return {
            "logic": logic,
            "type": "general",
            "need_graph": False,
            "need_rag": False,
            "mode": "single",
            "complexity": "simple",
            "resolved_plan": None,
        }

    mode = _normalize_retrieval_mode(getattr(output, "mode", "single"))
    complexity = _normalize_complexity(getattr(output, "complexity", "simple"))
    need_graph = bool(getattr(output, "need_graph", False))
    need_rag = bool(getattr(output, "need_rag", False))
    return {
        "logic": logic,
        "type": "rag_doc-query",
        "need_graph": need_graph,
        "need_rag": need_rag,
        "mode": mode,
        "complexity": complexity,
        "resolved_plan": resolve_execution_plan(
            need_graph=need_graph,
            need_rag=need_rag,
            mode=mode,
            complexity=complexity,
        ),
    }


async def route_and_plan_query(state: AgentState, *, config: RunnableConfig) -> dict[str, object]:
    """一次结构化调用完成顶层路由和检索能力规划。"""
    _ = config
    messages = build_safe_messages(ROUTING_DECISION_PROMPT, state.messages)
    output: RoutingDecisionOutput = await router_model.with_structured_output(
        RoutingDecisionOutput
    ).ainvoke(messages)
    return {"routing_decision": _build_routing_decision(output)}


def routing_decision_edge(state: AgentState) -> RoutingDecisionEdgeName:
    """根据统一决策选择 general 回复或独立 Guardrails。

    返回值是 path map 的键，不是最终节点展示名：
    - general → respond_to_general_query
    - rag_doc-query → guardrails_node
    """
    if state.routing_decision["type"] == "general":
        return "respond_to_general_query"
    return "guardrails_node"


async def respond_to_general_query(
    state: AgentState,
    *,
    config: RunnableConfig,
) -> dict[str, list[BaseMessage]]:
    """处理通用查询：闲聊、追问等。注入记忆上下文增强回复。"""
    system_prompt = await build_general_query_system_prompt(
        state=state,
        config=config,
        general_query_system_prompt=GENERAL_QUERY_SYSTEM_PROMPT,
    )
    messages = build_safe_messages(system_prompt, state.messages)
    response = await agent_model.ainvoke(messages)
    return {"messages": [response]}


async def guardrails_node(
    state: AgentState,
    *,
    config: RunnableConfig,
) -> dict[str, list[BaseMessage] | str]:
    """守卫节点：检查问题是否在业务范围内，拦截恶意输入。"""
    _ = config
    wrapped_question, _ = wrap_user_message(question_from_state(state))
    guardrails_output = await ainvoke_structured_question_output(
        system_prompt=GUARDRAILS_SYSTEM_PROMPT,
        human_prompt=f"参考此范围描述来决策:\n{SCOPE_DESCRIPTION}\nQuestion: {{question}}",
        model=guardrails_model,
        output_schema=GuardrailsDecision,
        question=wrapped_question,
    )

    if guardrails_output.decision == "end":
        return {
            "messages": [AIMessage(content=_GUARDRAILS_BLOCK_MESSAGE)],
            "next_action": "end",
        }
    return {"next_action": "continue"}


def guardrails_edge(state: AgentState) -> GuardrailsEdgeName:
    """守卫后的路由：continue → 已解析执行计划，end → 直接回复。"""
    if state.next_action == "end":
        return "after_response"
    return routing_decision_execution_edge(state)


def routing_decision_execution_edge(state: AgentState) -> RetrievalEdgeName:
    """按统一决策的 resolved_plan 进入执行节点；异常状态回退 REACT。"""
    raw = state.routing_decision
    if raw.get("type") != "rag_doc-query":
        return "execute_react"

    resolved = raw.get("resolved_plan")
    if isinstance(resolved, str) and resolved in _RETRIEVAL_EDGE_MAP:
        return _RETRIEVAL_EDGE_MAP[resolved]  # pyright: ignore[reportArgumentType]

    recomputed = resolve_execution_plan(
        need_graph=bool(raw.get("need_graph")),
        need_rag=bool(raw.get("need_rag")),
        mode=_normalize_retrieval_mode(raw.get("mode")),
        complexity=_normalize_complexity(raw.get("complexity")),
    )
    return _RETRIEVAL_EDGE_MAP[recomputed]


__all__ = [
    "build_general_query_system_prompt",
    "guardrails_edge",
    "guardrails_node",
    "route_and_plan_query",
    "resolve_execution_plan",
    "respond_to_general_query",
    "routing_decision_edge",
    "routing_decision_execution_edge",
]
