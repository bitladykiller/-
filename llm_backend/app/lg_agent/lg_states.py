"""Agent 状态定义。v3.7: Router 2 分类，新增 RetrievalPlan。"""
from pydantic import BaseModel, Field
from dataclasses import dataclass, field
from typing import Annotated, Literal, TypedDict, List
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


class Router(TypedDict):
    """顶层路由分类 — 2 路。"""
    logic: str
    type: Literal["general", "rag_doc-query"]


class RetrievalPlan(TypedDict):
    """检索计划路由 — 5 路。"""
    logic: str
    plan: Literal["GRAPH_ONLY", "RAG_ONLY", "PARALLEL", "GRAPH_THEN_RAG", "AGENT_REACT"]


class GradeHallucinations(BaseModel):
    """幻觉检测：1=基于事实，0=可能幻觉。"""
    binary_score: str = Field(description="'1' or '0'")


@dataclass(kw_only=True)
class InputState:
    """Agent 输入状态。"""
    messages: Annotated[list[AnyMessage], add_messages]


@dataclass(kw_only=True)
class AgentState(InputState):
    """Agent 完整状态。"""
    router: Router = field(default_factory=lambda: Router(type="general", logic=""))
    retrieval_plan: RetrievalPlan | None = None
    steps: list[str] = field(default_factory=list)
    question: str = field(default_factory=str)
    answer: str = field(default_factory=str)
    hallucination: GradeHallucinations = field(default_factory=lambda: GradeHallucinations(binary_score="0"))
    react_round: int = 0  # AgentReAct 迭代计数
