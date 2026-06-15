import asyncio
import sys
import types

import app.chat.application.task_queue as task_queue_module


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
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = task_queue_module._TaskManager(store)
        store.values["task:doc_parse:task-1"] = (
            '{"task_id": "task-1", "status": "completed", '
            '"updated_at": "2026-01-02T03:04:05", "result": {"ok": true}}'
        )

        loaded = await manager.get_status("task-1")

        assert loaded is not None
        assert loaded["task_id"] == "task-1"
        assert loaded["status"] == "completed"
        assert loaded["result"] == {"ok": True}

    asyncio.run(scenario())


def test_task_manager_get_status_rejects_invalid_input() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = task_queue_module._TaskManager(store)

        assert await manager.get_status("missing") is None

        store.values["task:doc_parse:bad-json"] = "not-json"
        assert await manager.get_status("bad-json") is None

        store.values["task:doc_parse:bad-schema"] = '{"task_id": 1}'
        assert await manager.get_status("bad-schema") is None

    asyncio.run(scenario())


def test_write_and_read_task_status_round_trip() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = task_queue_module._TaskManager(store)

        await manager._write_task_status(
            "task-2",
            "running",
        )

        payload = await manager.get_status("task-2")
        assert payload is not None
        assert payload["task_id"] == "task-2"
        assert payload["status"] == "running"

    asyncio.run(scenario())


def test_task_manager_submit_registers_named_background_task() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = task_queue_module._TaskManager(store)
        release = asyncio.Event()
        started = asyncio.Event()

        async def job() -> None:
            started.set()
            await release.wait()

        task_id = await manager.submit(job)
        await asyncio.wait_for(started.wait(), timeout=1)

        assert len(manager._pending_tasks) == 1
        task = next(iter(manager._pending_tasks))
        assert task.get_name() == f"task:{task_id}"

        release.set()
        await task
        await asyncio.sleep(0)

        assert task not in manager._pending_tasks

    asyncio.run(scenario())


def test_task_manager_submit_and_complete_task() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = task_queue_module._TaskManager(store)
        fake_logger = FakeLogger()
        original_logger = task_queue_module.logger

        async def job(value: int) -> dict[str, int]:
            return {"value": value}

        task_queue_module.logger = fake_logger
        try:
            task_id = await manager.submit(job, 7)
            await asyncio.sleep(0.01)
        finally:
            task_queue_module.logger = original_logger

        status = await manager.get_status(task_id)
        assert status is not None
        assert status["status"] == "completed"
        assert status["result"] == {"value": 7}
        assert ("任务完成 | task_id=%s", (task_id,)) in fake_logger.infos

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
        fake_logger = FakeLogger()
        original_logger = task_queue_module.logger

        async def job() -> None:
            raise RuntimeError("boom")

        task_queue_module.logger = fake_logger
        try:
            task_id = await manager.submit(job)
            await asyncio.sleep(0.01)
        finally:
            task_queue_module.logger = original_logger

        status = await manager.get_status(task_id)
        assert status is not None
        assert status["status"] == "failed"
        assert status["error"] == "boom"

        raw_key = next(iter(store.values))
        assert task_id in raw_key
        raw_payload = store.values[raw_key]
        assert raw_payload is not None
        assert len(fake_logger.errors) == 1
        message, args, exc_info = fake_logger.errors[0]
        assert message == "任务失败 | task_id=%s | %s"
        assert args[0] == task_id
        assert str(args[1]) == "boom"
        assert exc_info is True

    asyncio.run(scenario())


def test_get_task_manager_reuses_existing_instance(monkeypatch) -> None:
    async def scenario() -> None:
        task_queue_module._runtime_instance = None
        created_clients: list[FakeTaskStore] = []

        def fake_from_url(redis_url: str, *, decode_responses: bool) -> FakeTaskStore:
            assert redis_url == "redis://test"
            assert decode_responses is True
            client = FakeTaskStore()
            created_clients.append(client)
            return client

        monkeypatch.setattr(
            task_queue_module.aioredis,
            "from_url",
            fake_from_url,
        )
        monkeypatch.setitem(
            sys.modules,
            "app.shared.core.config",
            types.SimpleNamespace(settings=types.SimpleNamespace(REDIS_URL="redis://test")),
        )

        first = await task_queue_module.get_task_manager()
        second = await task_queue_module.get_task_manager()

        assert first is second
        assert len(created_clients) == 1

        await task_queue_module.close_task_manager()
        assert created_clients[0].closed is True
        assert task_queue_module._runtime_instance is None

    asyncio.run(scenario())


def test_close_task_manager_swallows_close_errors() -> None:
    async def scenario() -> None:
        failing_store = FakeTaskStore(fail_on_close=True)
        task_queue_module._runtime_instance = task_queue_module._TaskManager(failing_store)

        await task_queue_module.close_task_manager()

        assert failing_store.closed is True
        assert task_queue_module._runtime_instance is None

    asyncio.run(scenario())
