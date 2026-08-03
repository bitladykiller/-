import asyncio
import sys
import types

import app.shared.background_tasks as background_tasks_module
from app.shared.background_tasks import (
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
        manager = background_tasks_module._TaskManager(store)

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
        manager = background_tasks_module._TaskManager(store)

        class SampleJob:
            async def __call__(self) -> None:
                return None

        info_logs: list[tuple[str, tuple[object, ...]]] = []
        original_logger = background_tasks_module.logger

        class FakeModuleLogger:
            def info(self, msg: str, *args, **kwargs) -> None:
                info_logs.append((msg, args))

            def error(self, msg: str, *args, **kwargs) -> None:
                return None

        background_tasks_module.logger = FakeModuleLogger()
        try:
            task_id = await manager.submit(SampleJob())
            await asyncio.sleep(0.01)
        finally:
            background_tasks_module.logger = original_logger

        assert task_id
        assert any(log == ("任务已提交 | task_id=%s | func=%s", (task_id, "SampleJob")) for log in info_logs)

    asyncio.run(scenario())


def test_task_manager_marks_failed_tasks() -> None:
    async def scenario() -> None:
        store = FakeTaskStore()
        manager = background_tasks_module._TaskManager(store)

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
            background_tasks_module,
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

        first = await background_tasks_module.get_task_manager()
        second = await background_tasks_module.get_task_manager()

        assert first is second
        assert len(created_clients) == 1
        assert container.task_manager is first

    asyncio.run(scenario())


# 关闭路径的唯一所有者是 AppContainer.close()，因此断言直接打在它身上。


async def test_app_container_close_releases_task_manager() -> None:
    from app.platform.container import AppContainer

    store = FakeTaskStore()
    container = AppContainer(task_manager=background_tasks_module._TaskManager(store))

    await container.close()

    assert store.closed is True
    assert container.task_manager is None


async def test_app_container_close_swallows_task_manager_errors() -> None:
    """关连接失败不能让停机流程炸掉，后续资源仍要继续释放。"""
    from app.platform.container import AppContainer

    failing_store = FakeTaskStore(fail_on_close=True)
    container = AppContainer(task_manager=background_tasks_module._TaskManager(failing_store))

    await container.close()

    assert failing_store.closed is True
    assert container.task_manager is None


# ---------------------------------------------------------------------- #
# 进程重启后的孤儿任务收敛
# ---------------------------------------------------------------------- #


class ScannableTaskStore(FakeTaskStore):
    """支持 scan_iter 的状态存储替身。"""

    def __init__(self) -> None:
        super().__init__()
        self.scan_patterns: list[str] = []

    async def scan_iter(self, match: str):
        self.scan_patterns.append(match)
        for key in list(self.values):
            yield key


#: 显式表示"这条记录没有 worker_id 字段"（模拟本次改动之前写入的历史数据）
_NO_WORKER_FIELD = object()


def _seed(store: ScannableTaskStore, task_id: str, status, worker_id: object) -> None:
    payload = background_tasks_module.build_task_status_payload(
        task_id,
        status,
        worker_id="" if worker_id is _NO_WORKER_FIELD else str(worker_id),
    )
    if worker_id is _NO_WORKER_FIELD:
        payload.pop("worker_id", None)
    prefix = background_tasks_module._TASK_CFG.task_key_prefix
    store.values[f"{prefix}{task_id}"] = background_tasks_module.dump_task_status_payload(payload)


def _status_of(store: ScannableTaskStore, task_id: str) -> str:
    prefix = background_tasks_module._TASK_CFG.task_key_prefix
    payload = background_tasks_module.load_task_status_payload(store.values[f"{prefix}{task_id}"])
    assert payload is not None
    return payload["status"]


def test_is_orphaned_task_only_flags_unfinished_foreign_records() -> None:
    unfinished_mine = {"status": "running", "worker_id": "me"}
    unfinished_other = {"status": "running", "worker_id": "old"}
    unfinished_legacy = {"status": "pending"}
    finished_other = {"status": "completed", "worker_id": "old"}

    assert background_tasks_module.is_orphaned_task(unfinished_mine, current_worker_id="me") is False
    assert background_tasks_module.is_orphaned_task(unfinished_other, current_worker_id="me") is True
    # 历史记录没有 worker_id，按旧进程处理
    assert background_tasks_module.is_orphaned_task(unfinished_legacy, current_worker_id="me") is True
    assert background_tasks_module.is_orphaned_task(finished_other, current_worker_id="me") is False


async def test_reconcile_marks_previous_worker_tasks_as_interrupted() -> None:
    """重启后旧任务不能永远停在 running，否则前端一直转圈。"""
    store = ScannableTaskStore()
    current = background_tasks_module.WORKER_ID
    _seed(store, "orphan-running", background_tasks_module.TaskStatus.RUNNING, "old-worker")
    _seed(store, "orphan-pending", background_tasks_module.TaskStatus.PENDING, "old-worker")
    _seed(store, "legacy-running", background_tasks_module.TaskStatus.RUNNING, _NO_WORKER_FIELD)
    _seed(store, "mine-running", background_tasks_module.TaskStatus.RUNNING, current)
    _seed(store, "done", background_tasks_module.TaskStatus.COMPLETED, "old-worker")

    manager = background_tasks_module._TaskManager(store)
    reconciled = await manager.reconcile_orphaned_tasks()

    assert reconciled == 3
    assert _status_of(store, "orphan-running") == "interrupted"
    assert _status_of(store, "orphan-pending") == "interrupted"
    # 无 worker_id 的历史记录同样按孤儿处理
    assert _status_of(store, "legacy-running") == "interrupted"
    # 当前进程自己的任务和已终结的任务不动
    assert _status_of(store, "mine-running") == "running"
    assert _status_of(store, "done") == "completed"


async def test_reconcile_records_reason_for_interruption() -> None:
    store = ScannableTaskStore()
    _seed(store, "orphan", background_tasks_module.TaskStatus.RUNNING, "old-worker")

    await background_tasks_module._TaskManager(store).reconcile_orphaned_tasks()

    prefix = background_tasks_module._TASK_CFG.task_key_prefix
    payload = background_tasks_module.load_task_status_payload(store.values[f"{prefix}orphan"])
    assert payload is not None
    assert payload["error"] == background_tasks_module.INTERRUPTED_ERROR_MESSAGE
    # 收敛记录归属当前进程，避免下次启动重复处理
    assert payload["worker_id"] == background_tasks_module.WORKER_ID


async def test_reconcile_returns_zero_when_scan_fails() -> None:
    """扫描失败不能阻断启动。"""

    class BrokenStore(ScannableTaskStore):
        async def scan_iter(self, match: str):
            raise RuntimeError("redis unavailable")
            yield  # pragma: no cover

    manager = background_tasks_module._TaskManager(BrokenStore())

    assert await manager.reconcile_orphaned_tasks() == 0


async def test_submit_stamps_current_worker_id() -> None:
    store = ScannableTaskStore()
    manager = background_tasks_module._TaskManager(store)

    async def noop() -> str:
        return "ok"

    task_id = await manager.submit(noop)
    status = await manager.get_status(task_id)

    assert status is not None
    assert status["worker_id"] == background_tasks_module.WORKER_ID
