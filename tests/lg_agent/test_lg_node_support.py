from langchain_core.messages import AIMessage, HumanMessage

import app.chat.infrastructure.graph.decision_nodes as decision_nodes
from app.chat.infrastructure.graph.decision_nodes import (
    build_guardrails_block_response,
    build_memory_augmented_system_prompt,
    build_wrapped_question,
    route_guardrails_action,
    route_query_type,
    route_retrieval_plan,
)
from app.chat.infrastructure.graph.lifecycle_nodes import (
    build_after_response_payload,
)


def test_route_helpers_map_to_expected_node_names() -> None:
    assert route_query_type("general") == "respond_to_general_query"
    assert route_query_type("rag_doc-query") == "retrieval_plan_router"
    assert route_guardrails_action("end") == "after_response"
    assert route_guardrails_action("continue") == "retrieval_plan_route"
    assert route_retrieval_plan("GRAPH_ONLY") == "execute_graph_only"
    assert route_retrieval_plan("GRAPH_THEN_RAG") == "execute_then"
    assert route_retrieval_plan("UNKNOWN") == "execute_react"
    assert route_retrieval_plan(None) == "execute_react"


def test_prompt_helpers_keep_wrapped_question_and_memory_suffix() -> None:
    assert build_memory_augmented_system_prompt(
        system_prompt="system",
        memory_context=" memory",
    ) == "system memory"
    assert build_memory_augmented_system_prompt(
        system_prompt="system",
        memory_context="",
    ) == "system"
    wrapped = build_wrapped_question("请查一下空调")
    assert "请查一下空调" in wrapped
    assert "<user_message>" in wrapped


def test_build_guardrails_block_response_uses_stable_reply() -> None:
    response = build_guardrails_block_response()

    assert response["next_action"] == "end"
    assert len(response["messages"]) == 1
    assert response["messages"][0].content == decision_nodes._GUARDRAILS_BLOCK_MESSAGE


def test_build_after_response_payload_uses_latest_complete_message_pair() -> None:
    payload = build_after_response_payload(
        tenant_id="tenant-1",
        user_id="user-2",
        session_id="session-3",
        messages=[
            HumanMessage(content="旧问题"),
            AIMessage(content="旧回答"),
            HumanMessage(content="新问题"),
            AIMessage(content="中间状态"),
            AIMessage(content="最终回答"),
        ],
    )

    assert payload == {
        "tenant_id": "tenant-1",
        "user_id": "user-2",
        "session_id": "session-3",
        "user_message": "新问题",
        "assistant_message": "最终回答",
    }


def test_build_after_response_payload_returns_none_without_complete_pair() -> None:
    assert build_after_response_payload(
        tenant_id="tenant-1",
        user_id="user-2",
        session_id="session-3",
        messages=[HumanMessage(content="只有用户消息")],
    ) is None
