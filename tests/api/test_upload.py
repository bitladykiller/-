import asyncio

import pytest
from fastapi import HTTPException

import app.api.upload as upload_api


class FakeUploadFile:
    def __init__(self, filename: str | None = "guide.pdf") -> None:
        self.filename = filename


class FakeTaskManager:
    def __init__(self, *, task_id: str = "task-1", status=None) -> None:
        self.task_id = task_id
        self.status = status
        self.submit_calls: list[tuple[object, tuple[object, ...]]] = []
        self.status_calls: list[str] = []

    async def submit(self, coro_func, *args):
        self.submit_calls.append((coro_func, args))
        return self.task_id

    async def get_status(self, task_id: str):
        self.status_calls.append(task_id)
        return self.status


def _run(awaitable):
    return asyncio.run(awaitable)


def test_process_upload_runs_validation_storage_and_task_submission(monkeypatch) -> None:
    file = FakeUploadFile("guide.pdf")
    manager = FakeTaskManager(task_id="task-9")
    file_info = {"path": "uploads/guide.pdf", "filename": "guide.pdf", "user_id": 7}
    captured: list[tuple[str, object]] = []

    def fake_validate_upload(upload_file):
        captured.append(("validate", upload_file))

    async def fake_store_upload(upload_file, user_id: int):
        captured.append(("store", (upload_file, user_id)))
        return file_info

    async def fake_get_task_manager():
        return manager

    monkeypatch.setattr(upload_api, "validate_upload", fake_validate_upload)
    monkeypatch.setattr(upload_api, "store_upload", fake_store_upload)
    monkeypatch.setattr(upload_api, "get_task_manager", fake_get_task_manager)

    response = _run(upload_api._process_upload(file, 7))

    assert captured == [
        ("validate", file),
        ("store", (file, 7)),
    ]
    assert len(manager.submit_calls) == 1
    submitted_func, submitted_args = manager.submit_calls[0]
    assert submitted_func.__self__.__class__ is upload_api.IndexingService
    assert submitted_func.__name__ == "process_file"
    assert submitted_args == (file_info,)
    assert response == {
        **file_info,
        "task_id": "task-9",
        "message": upload_api.build_upload_accepted_response(file_info, "task-9")["message"],
    }


def test_get_upload_status_or_raise_returns_status_and_raises_404(monkeypatch) -> None:
    async def fake_get_task_manager_ok():
        return FakeTaskManager(status={"status": "running"})

    monkeypatch.setattr(upload_api, "get_task_manager", fake_get_task_manager_ok)
    assert _run(upload_api._get_upload_status_or_raise("task-ok")) == {"status": "running"}

    async def fake_get_task_manager_missing():
        return FakeTaskManager(status=None)

    monkeypatch.setattr(upload_api, "get_task_manager", fake_get_task_manager_missing)
    with pytest.raises(HTTPException) as exc:
        _run(upload_api._get_upload_status_or_raise("task-missing"))

    assert exc.value.status_code == 404
    assert exc.value.detail == "任务不存在: task-missing"
