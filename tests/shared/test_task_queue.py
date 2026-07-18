import asyncio
import sys
import types

import app.shared.task_queue as task_queue_module
from app.shared.task_queue import (
    TaskStatus,
    build_task_status_payload,
    dump_task_status_payload,
    load_task_status_payload,
    read_task_status,
    run_task_with_status_updates,
    spawn_tracked_task,
    write_task_status,
)


class FakeTaskStore:
    def __init__(self, *, fail_on_close: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.closed = False
        self.fail_on_close = fail_on_close

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def close(self) -> None:
        self.closed = True
        if self.fail_on_close:
            raise RuntimeError("close failed")


class FakeLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, tuple[object, ...]]] = []
        self.errors: list[tuple[str, tuple[object, ...], bool]] = []

    def info(self, msg: str, *args, **kwargs) -> None:
        self.infos.append((msg, args))

    def error(self, msg: str, *args, **kwargs) -> None:
        self.errors.append((msg, args, kwargs.get("exc_info", False)))


def test_task_status_payload_round_trip() -> None:
    payload = build_task_status_payload(
        "task-1",
        TaskStatus.COMPLETED,
        result={"ok": True},
    )

    loaded = load_task_status_payload(dump_task_status_payload(payload))

    assert loaded is not None
    assert loaded["task_id"] == "task-1"
    assert loaded["status"] == "completed"
    assert loaded["result"] == {"ok": True}


def test_load_task_status_payload_rejects_invalid_input() -> None:
    assert load_task_status_payload(None) is None
    assert load_task_status_payload("not-json") is None
    assert load_task_status_payload('{"task_id": 1}') is None


def test_write_and_read_task_status_round_trip() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()

        await write_task_status(
            store,
            "task-2",
            TaskStatus.RUNNING,
        )

        raw = await store.get("task:doc_parse:task-2")
        assert raw is not None
        payload = await read_task_status(store, "task-2")
        assert payload is not None
        assert payload["task_id"] == "task-2"
        assert payload["status"] == "running"

    asyncio.run(scenario())


def test_spawn_tracked_task_registers_named_background_task() -> None:
    async def scenario() -> None:
        pending_tasks: set[asyncio.Task[None]] = set()
        finished: list[str] = []

        async def job() -> None:
            finished.append(asyncio.current_task().get_name())

        spawn_tracked_task(pending_tasks, "abc123", job())

        assert len(pending_tasks) == 1
        task = next(iter(pending_tasks))
        assert task.get_name() == "task:abc123"

        await task
        await asyncio.sleep(0)

        assert finished == ["task:abc123"]
        assert task not in pending_tasks

    asyncio.run(scenario())


def test_run_task_with_status_updates_marks_completed_and_logs() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        logger = FakeLogger()

        async def job(value: int) -> dict[str, int]:
            return {"value": value}

        await run_task_with_status_updates(store, logger, "task-3", job, 9)

        payload = await read_task_status(store, "task-3")
        assert payload is not None
        assert payload["status"] == "completed"
        assert payload["result"] == {"value": 9}
        assert logger.infos == [("任务完成 | task_id=%s", ("task-3",))]
        assert logger.errors == []

    asyncio.run(scenario())


def test_run_task_with_status_updates_marks_failed_and_logs() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        logger = FakeLogger()

        async def job() -> None:
            raise RuntimeError("boom")

        await run_task_with_status_updates(store, logger, "task-4", job)

        payload = await read_task_status(store, "task-4")
        assert payload is not None
        assert payload["status"] == "failed"
        assert payload["error"] == "boom"
        assert logger.infos == []
        assert len(logger.errors) == 1
        message, args, exc_info = logger.errors[0]
        assert message == "任务失败 | task_id=%s | %s"
        assert args[0] == "task-4"
        assert str(args[1]) == "boom"
        assert exc_info is True

    asyncio.run(scenario())


def test_task_manager_submit_and_complete_task() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = task_queue_module._TaskManager(store)

        async def job(value: int) -> dict[str, int]:
            return {"value": value}

        task_id = await manager.submit(job, 7)
        await asyncio.sleep(0.01)

        status = await manager.get_status(task_id)
        assert status is not None
        assert status["status"] == "completed"
        assert status["result"] == {"value": 7}

    asyncio.run(scenario())


def test_task_manager_submit_logs_callable_name_for_callable_object() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = task_queue_module._TaskManager(store)

        class SampleJob:
            async def __call__(self) -> None:
                return None

        info_logs: list[tuple[str, tuple[object, ...]]] = []
        original_logger = task_queue_module.logger

        class FakeModuleLogger:
            def info(self, msg: str, *args, **kwargs) -> None:
                info_logs.append((msg, args))

            def error(self, msg: str, *args, **kwargs) -> None:
                return None

        task_queue_module.logger = FakeModuleLogger()
        try:
            task_id = await manager.submit(SampleJob())
            await asyncio.sleep(0.01)
        finally:
            task_queue_module.logger = original_logger

        assert task_id
        assert any(log == ("任务已提交 | task_id=%s | func=%s", (task_id, "SampleJob")) for log in info_logs)

    asyncio.run(scenario())


def test_task_manager_marks_failed_tasks() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = task_queue_module._TaskManager(store)

        async def job() -> None:
            raise RuntimeError("boom")

        task_id = await manager.submit(job)
        await asyncio.sleep(0.01)

        status = await manager.get_status(task_id)
        assert status is not None
        assert status["status"] == "failed"
        assert status["error"] == "boom"

        raw_key = next(iter(store.values))
        assert task_id in raw_key
        raw_payload = store.values[raw_key]
        assert raw_payload is not None

    asyncio.run(scenario())


def test_get_task_manager_reuses_existing_instance(monkeypatch) -> None:
    async def scenario() -> None:
        created_clients: list[FakeTaskStore] = []

        class FakeContainer:
            def __init__(self) -> None:
                self.task_manager = None

        container = FakeContainer()

        async def fake_get_container():
            return container

        def fake_create_redis_client(redis_url: str) -> FakeTaskStore:
            assert redis_url == "redis://test"
            client = FakeTaskStore()
            created_clients.append(client)
            return client

        monkeypatch.setattr(
            task_queue_module,
            "create_redis_client",
            fake_create_redis_client,
        )
        monkeypatch.setitem(
            sys.modules,
            "app.shared.core.config",
            types.SimpleNamespace(settings=types.SimpleNamespace(REDIS_URL="redis://test")),
        )
        monkeypatch.setitem(
            sys.modules,
            "app.platform.container",
            types.SimpleNamespace(get_container=fake_get_container),
        )

        first = await task_queue_module.get_task_manager()
        second = await task_queue_module.get_task_manager()

        assert first is second
        assert len(created_clients) == 1
        assert container.task_manager is first

        await task_queue_module.close_task_manager()
        assert created_clients[0].closed is True
        assert container.task_manager is None

    asyncio.run(scenario())


def test_close_task_manager_swallows_close_errors(monkeypatch) -> None:
    async def scenario() -> None:
        failing_store = FakeTaskStore(fail_on_close=True)

        class FakeContainer:
            def __init__(self) -> None:
                self.task_manager = task_queue_module._TaskManager(failing_store)

        container = FakeContainer()

        async def fake_get_container():
            return container

        monkeypatch.setitem(
            sys.modules,
            "app.platform.container",
            types.SimpleNamespace(get_container=fake_get_container),
        )

        await task_queue_module.close_task_manager()

        assert failing_store.closed is True
        assert container.task_manager is None

    asyncio.run(scenario())
