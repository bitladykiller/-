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


def test_validate_upload_rejects_unsupported_extension_and_missing_content_type() -> None:
    with pytest.raises(HTTPException) as unsupported_exc:
        upload_api.validate_upload(
            FakeUploadFile(
                filename="demo.txt",
                content_type="text/plain",
                content=b"hello",
            )
        )
    assert unsupported_exc.value.status_code == 400
    assert unsupported_exc.value.detail == "不支持的文件类型: .txt"

    with pytest.raises(HTTPException) as missing_type_exc:
        upload_api.validate_upload(
            FakeUploadFile(
                filename="demo.pdf",
                content_type=None,
            )
        )
    assert missing_type_exc.value.status_code == 400
    assert missing_type_exc.value.detail == "无法识别文件类型"


def test_read_upload_content_accepts_matching_signature_and_unknown_extension() -> None:
    assert (
        _run(
            upload_api.read_upload_content(
                FakeUploadFile(filename="demo.pdf", content=b"%PDF-1.7"),
                max_upload_size_bytes=10,
                file_size_exceeded_detail="文件大小超过限制 (50MB)",
                content_extension_mismatch_detail="文件内容与扩展名不匹配: {extension}",
            )
        )
        == b"%PDF-1.7"
    )
    assert (
        _run(
            upload_api.read_upload_content(
                FakeUploadFile(
                    filename="demo.unknown",
                    content_type="application/octet-stream",
                    content=b"whatever",
                ),
                max_upload_size_bytes=10,
                file_size_exceeded_detail="文件大小超过限制 (50MB)",
                content_extension_mismatch_detail="文件内容与扩展名不匹配: {extension}",
            )
        )
        == b"whatever"
    )


def test_read_upload_content_rejects_oversize_and_signature_mismatch() -> None:
    with pytest.raises(HTTPException) as oversize_exc:
        _run(
            upload_api.read_upload_content(
                FakeUploadFile(content=b"12345"),
                max_upload_size_bytes=4,
                file_size_exceeded_detail="文件大小超过限制 (50MB)",
                content_extension_mismatch_detail="文件内容与扩展名不匹配: {extension}",
            )
        )
    assert oversize_exc.value.status_code == 400
    assert oversize_exc.value.detail == "文件大小超过限制 (50MB)"

    with pytest.raises(HTTPException) as mismatch_exc:
        _run(
            upload_api.read_upload_content(
                FakeUploadFile(content=b"PK\x03\x04"),
                max_upload_size_bytes=4,
                file_size_exceeded_detail="文件大小超过限制 (50MB)",
                content_extension_mismatch_detail="文件内容与扩展名不匹配: {extension}",
            )
        )
    assert mismatch_exc.value.status_code == 400
    assert mismatch_exc.value.detail == "文件内容与扩展名不匹配: .pdf"


def test_store_upload_writes_file_and_returns_stable_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeUUIDModule:
        NAMESPACE_DNS = object()

        @staticmethod
        def uuid5(namespace, value):
            return "user-uuid"

    monkeypatch.setattr(upload_api, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(upload_api, "uuid", FakeUUIDModule)
    monkeypatch.setattr(upload_api, "datetime", FakeDateTime)

    file_info = _run(upload_api._store_upload(FakeUploadFile(filename="manual.pdf"), 3))

    assert file_info == {
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
    assert (tmp_path / "user-uuid" / "20260102_030405" / "manual_20260102_030405.pdf").read_bytes() == b"%PDF-1.7"


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
    monkeypatch.setattr(upload_api, "_store_upload", fake_store_upload)
    monkeypatch.setattr(upload_api, "get_task_manager", fake_get_task_manager)

    response = _run(upload_api.upload_file(file, 7))

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
        "message": "文件已上传，后台正在解析索引。请通过 task_id 查询进度。",
    }


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
