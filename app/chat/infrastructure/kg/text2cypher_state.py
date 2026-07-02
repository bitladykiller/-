"""
This file contains classes that manage the state of a Text2Cypher Agent or subgraph.
"""

from operator import add
from typing import Annotated, Any

from typing_extensions import TypedDict


class CypherInputState(TypedDict):
    task: Annotated[list[object], add]


class CypherState(TypedDict):
    task: Annotated[list[object], add]
    statement: str
    parameters: dict[str, Any] | None
    errors: list[str]
    records: list[dict[str, Any]]
    next_action_cypher: str
    attempts: int
    steps: Annotated[list[str], add]


class CypherOutputState(TypedDict):
    task: Annotated[list[object], add]
    statement: str
    parameters: dict[str, Any] | None
    errors: list[str]
    records: list[dict[str, Any]]
    steps: list[str]
