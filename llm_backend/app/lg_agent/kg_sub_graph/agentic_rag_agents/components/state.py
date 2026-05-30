"""Agentic RAG 组件共享状态。"""
from operator import add
from typing import Annotated, List

from typing_extensions import TypedDict

from .text2cypher.state import CypherOutputState


class OverallState(TypedDict):
    question: str
    tasks: Annotated[list, add]
    next_action: str
    cyphers: Annotated[List[CypherOutputState], add]
    summary: str
    steps: Annotated[List[str], add]
