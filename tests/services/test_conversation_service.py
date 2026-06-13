import asyncio

import app.services.conversation_service as conversation_service


def _run(awaitable):
    return asyncio.run(awaitable)


def test_create_conversation_delegates_to_run_db_operation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_db_operation(action_name, operation, *operation_args, **context):
        captured["action_name"] = action_name
        captured["operation"] = operation
        captured["operation_args"] = operation_args
        captured["context"] = context
        return 123

    monkeypatch.setattr(conversation_service, "_run_db_operation", fake_run_db_operation)

    result = _run(conversation_service.ConversationService.create_conversation(5))

    assert result == 123
    assert captured == {
        "action_name": "create_conversation",
        "operation": conversation_service._create_conversation_record,
        "operation_args": (5,),
        "context": {"user_id": 5},
    }


def test_get_user_conversations_delegates_to_fetch_operation(monkeypatch) -> None:
    async def fake_run_db_operation(action_name, operation, *operation_args, **context):
        assert action_name == "get_user_conversations"
        assert operation is conversation_service._fetch_user_conversations
        assert operation_args == (8,)
        assert context == {"user_id": 8}
        return [{"id": 1}]

    monkeypatch.setattr(conversation_service, "_run_db_operation", fake_run_db_operation)

    result = _run(conversation_service.ConversationService.get_user_conversations(8))

    assert result == [{"id": 1}]


def test_delete_and_rename_delegate_to_expected_record_operations(monkeypatch) -> None:
    calls: list[tuple[str, object, tuple[object, ...], dict[str, object]]] = []

    async def fake_run_db_operation(action_name, operation, *operation_args, **context):
        calls.append((action_name, operation, operation_args, context))
        return None

    monkeypatch.setattr(conversation_service, "_run_db_operation", fake_run_db_operation)

    _run(conversation_service.ConversationService.delete_conversation(9))
    _run(conversation_service.ConversationService.update_conversation_name(9, "售后跟进"))

    assert calls == [
        (
            "delete_conversation",
            conversation_service._delete_conversation_record,
            (9,),
            {"conversation_id": 9},
        ),
        (
            "update_conversation_name",
            conversation_service._rename_conversation_record,
            (9, "售后跟进"),
            {"conversation_id": 9, "name": "售后跟进"},
        ),
    ]
