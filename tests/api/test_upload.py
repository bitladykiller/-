import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.api.upload as upload_api


class FakeNow:
    def strftime(self, fmt: str) -> str:
        assert fmt == "%Y%m%d_%H%M%S"
        return "20260102_030405"


class FakeDateTime:
    @classmethod
    def now(cls) -> FakeNow:
        return FakeNow()


class FakeUploadFile:
    def __init__(
        self,
        filename: str | None = "guide.pdf",
        *,
        content_type: str | None = "application/pdf",
        content: bytes = b"%PDF-1.7",
    ) -> None:
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self) -> bytes:
        return self._content


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


def test_upload_file_rejects_unsupported_extension_and_missing_content_type() -> None:
    with pytest.raises(HTTPException) as unsupported_exc:
        _run(
            upload_api.upload_file(
                FakeUploadFile(
                    filename="demo.txt",
                    content_type="text/plain",
                    content=b"hello",
                ),
                3,
            )
        )
    assert unsupported_exc.value.status_code == 400
    assert unsupported_exc.value.detail == "不支持的文件类型: .txt"

    with pytest.raises(HTTPException) as missing_type_exc:
        _run(
            upload_api.upload_file(
                FakeUploadFile(
                    filename="demo.pdf",
                    content_type=None,
                ),
                3,
            )
        )
    assert missing_type_exc.value.status_code == 400
    assert missing_type_exc.value.detail == "无法识别文件类型"


def test_upload_file_rejects_oversize_and_signature_mismatch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(upload_api, "UPLOAD_DIR", tmp_path)

    with pytest.raises(HTTPException) as oversize_exc:
        _run(
            upload_api.upload_file(
                FakeUploadFile(content=b"x" * (upload_api.MAX_UPLOAD_SIZE_BYTES + 1)),
                3,
            )
        )
    assert oversize_exc.value.status_code == 400
    assert oversize_exc.value.detail == "文件大小超过限制 (50MB)"

    with pytest.raises(HTTPException) as mismatch_exc:
        _run(
            upload_api.upload_file(
                FakeUploadFile(content=b"PK\x03\x04"),
                3,
            )
        )
    assert mismatch_exc.value.status_code == 400
    assert mismatch_exc.value.detail == "文件内容与扩展名不匹配: .pdf"


def test_upload_file_writes_file_and_returns_stable_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeUUIDModule:
        NAMESPACE_DNS = object()

        @staticmethod
        def uuid5(namespace, value):
            return "user-uuid"

    manager = FakeTaskManager(task_id="task-store")

    async def fake_get_task_manager():
        return manager

    monkeypatch.setattr(upload_api, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(upload_api, "uuid", FakeUUIDModule)
    monkeypatch.setattr(upload_api, "datetime", FakeDateTime)
    monkeypatch.setattr(upload_api, "get_task_manager", fake_get_task_manager)

    response = _run(upload_api.upload_file(FakeUploadFile(filename="manual.pdf"), 3))

    file_info = {
        "filename": "manual_20260102_030405.pdf",
        "original_name": "manual.pdf",
        "size": len(b"%PDF-1.7"),
        "type": "application/pdf",
        "path": (tmp_path / "user-uuid" / "20260102_030405" / "manual_20260102_030405.pdf").as_posix(),
        "user_id": 3,
        "user_uuid": "user-uuid",
        "upload_time": "20260102_030405",
        "directory": (tmp_path / "user-uuid" / "20260102_030405").as_posix(),
    }

    assert response == {
        **file_info,
        "task_id": "task-store",
        "message": "文件已上传，后台正在解析索引。请通过 task_id 查询进度。",
    }
    assert len(manager.submit_calls) == 1
    submitted_func, submitted_args = manager.submit_calls[0]
    assert submitted_func.__self__.__class__ is upload_api.IndexingService
    assert submitted_func.__name__ == "process_file"
    assert submitted_args == (file_info,)
    assert (tmp_path / "user-uuid" / "20260102_030405" / "manual_20260102_030405.pdf").read_bytes() == b"%PDF-1.7"


def test_get_upload_status_or_raise_returns_status_and_raises_404(monkeypatch) -> None:
    async def fake_get_task_manager_ok():
        return FakeTaskManager(status={"status": "running"})

    monkeypatch.setattr(upload_api, "get_task_manager", fake_get_task_manager_ok)
    assert _run(upload_api.get_upload_status("task-ok")) == {"status": "running"}

    async def fake_get_task_manager_missing():
        return FakeTaskManager(status=None)

    monkeypatch.setattr(upload_api, "get_task_manager", fake_get_task_manager_missing)
    with pytest.raises(HTTPException) as exc:
        _run(upload_api.get_upload_status("task-missing"))

    assert exc.value.status_code == 404
    assert exc.value.detail == "任务不存在: task-missing"
