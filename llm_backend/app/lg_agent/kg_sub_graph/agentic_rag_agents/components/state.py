"""Agentic RAG 组件共享状态。仅保留仍被使用的类型。"""
from operator import add
from typing import Annotated, Any, Dict, List, Optional

from typing_extensions import TypedDict

from .text2cypher.state import CypherOutputState


class PredefinedCypherInputState(TypedDict):
    task: str
    query_name: str
    query_parameters: Dict[str, Any]
    steps: List[str]


class OverallState(TypedDict):
    question: str
    tasks: Annotated[list, add]
    next_action: str
    cyphers: Annotated[List[CypherOutputState], add]
    summary: str
    steps: Annotated[List[str], add]
