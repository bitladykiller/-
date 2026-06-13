import asyncio

from app.services.task_queue import TaskManager
from app.services.task_queue_utils import (
    TaskStatus,
    build_task_key,
    build_task_status_payload,
    dump_task_status_payload,
    load_task_status_payload,
)


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


def test_task_manager_submit_and_complete_task() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = TaskManager(store)

        async def job(value: int) -> dict[str, int]:
            return {"value": value}

        task_id = await manager.submit(job, 7)
        await asyncio.sleep(0.01)

        status = await manager.get_status(task_id)
        assert status is not None
        assert status["status"] == "completed"
        assert status["result"] == {"value": 7}

    asyncio.run(scenario())


def test_task_manager_marks_failed_tasks() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = TaskManager(store)

        async def job() -> None:
            raise RuntimeError("boom")

        task_id = await manager.submit(job)
        await asyncio.sleep(0.01)

        status = await manager.get_status(task_id)
        assert status is not None
        assert status["status"] == "failed"
        assert status["error"] == "boom"

        raw_payload = await store.get(build_task_key(task_id))
        assert raw_payload is not None

    asyncio.run(scenario())
