import asyncio

from app.services.task_queue_support import (
    build_background_task_name,
    read_task_status,
    register_pending_task,
    run_task_with_status_updates,
    spawn_tracked_task,
    task_callable_name,
    write_task_status,
)
from app.services.task_queue_utils import TaskStatus, load_task_status_payload


class FakeTaskStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.closed = False

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def close(self) -> None:
        self.closed = True


class FakeLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, tuple[object, ...]]] = []
        self.errors: list[tuple[str, tuple[object, ...], bool]] = []

    def info(self, msg: str, *args, **kwargs) -> None:
        self.infos.append((msg, args))

    def error(self, msg: str, *args, **kwargs) -> None:
        self.errors.append((msg, args, kwargs.get("exc_info", False)))


def test_task_callable_name_prefers_function_name() -> None:
    async def sample_job() -> None:
        return None

    assert task_callable_name(sample_job) == "sample_job"


def test_task_callable_name_falls_back_to_class_name() -> None:
    class SampleJob:
        async def __call__(self) -> None:
            return None

    assert task_callable_name(SampleJob()) == "SampleJob"


def test_build_background_task_name() -> None:
    assert build_background_task_name("abc123") == "task:abc123"


def test_write_task_status_persists_payload() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()

        await write_task_status(
            store,
            "task-1",
            TaskStatus.COMPLETED,
            result={"ok": True},
        )

        raw = await store.get("task:doc_parse:task-1")
        assert raw is not None

        payload = load_task_status_payload(raw)
        assert payload is not None
        assert payload["task_id"] == "task-1"
        assert payload["status"] == "completed"
        assert payload["result"] == {"ok": True}

    asyncio.run(scenario())


def test_read_task_status_returns_payload_when_present() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        await write_task_status(
            store,
            "task-2",
            TaskStatus.RUNNING,
        )

        payload = await read_task_status(store, "task-2")
        assert payload is not None
        assert payload["task_id"] == "task-2"
        assert payload["status"] == "running"

    asyncio.run(scenario())


def test_register_pending_task_removes_completed_task() -> None:
    async def scenario() -> None:
        pending_tasks: set[asyncio.Task[None]] = set()

        async def job() -> None:
            return None

        task = asyncio.create_task(job())
        register_pending_task(pending_tasks, task)

        assert task in pending_tasks

        await task
        await asyncio.sleep(0)

        assert task not in pending_tasks

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
