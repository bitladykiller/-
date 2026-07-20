"""上传 API 单元测试。

使用轻量 Fake 对象替代 FastAPI UploadFile / 任务管理器，
并通过 cast 对齐生产函数签名，避免编辑器类型误报。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, TypeVar, cast

import app.api.upload as upload_api
import pytest
from fastapi import HTTPException, UploadFile

T = TypeVar("T")


class FakeNow:
    def strftime(self, fmt: str) -> str:
        assert fmt == "%Y%m%d_%H%M%S"
        return "20260102_030405"


class FakeDateTime:
    @classmethod
    def now(cls) -> FakeNow:
        return FakeNow()


class FakeUploadFile:
    """满足上传链路读取需求的最小 UploadFile 替身。"""

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

    async def read(self, size: int = -1) -> bytes:
        _ = size
        return self._content


def _as_upload(file: FakeUploadFile) -> UploadFile:
    """将测试替身转换为 UploadFile 类型，供生产函数签名使用。"""
    return cast(UploadFile, file)


class FakeTaskManager:
    def __init__(self, *, task_id: str = "task-1", status: dict[str, Any] | None = None) -> None:
        self.task_id = task_id
        self.status = status
        self.submit_calls: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []
        self.status_calls: list[str] = []

    async def submit(self, coro_func: Callable[..., Any], *args: Any) -> str:
        self.submit_calls.append((coro_func, args))
        return self.task_id

    async def get_status(self, task_id: str) -> dict[str, Any] | None:
        self.status_calls.append(task_id)
        return self.status


def _run(coro: Coroutine[Any, Any, T]) -> T:
    """在同步测试中执行协程。"""
    return asyncio.run(coro)


def test_validate_upload_rejects_unsupported_extension_and_missing_content_type() -> None:
    with pytest.raises(HTTPException) as unsupported_exc:
        upload_api.validate_upload(
            _as_upload(
                FakeUploadFile(
                    filename="demo.txt",
                    content_type="text/plain",
                    content=b"hello",
                )
            )
        )
    assert unsupported_exc.value.status_code == 400
    assert unsupported_exc.value.detail == "不支持的文件类型: .txt"

    with pytest.raises(HTTPException) as missing_type_exc:
        upload_api.validate_upload(
            _as_upload(
                FakeUploadFile(
                    filename="demo.pdf",
                    content_type=None,
                )
            )
        )
    assert missing_type_exc.value.status_code == 400
    assert missing_type_exc.value.detail == "无法识别文件类型"


def test_validate_upload_accepts_markdown_pdf_and_docx() -> None:
    """三种业务允许类型：Markdown / PDF / Word。"""
    upload_api.validate_upload(
        _as_upload(
            FakeUploadFile(
                filename="notes.md",
                content_type="text/markdown",
                content=b"# hello",
            )
        )
    )
    upload_api.validate_upload(
        _as_upload(
            FakeUploadFile(
                filename="guide.markdown",
                content_type="text/plain",
                content=b"# guide",
            )
        )
    )
    upload_api.validate_upload(
        _as_upload(FakeUploadFile(filename="manual.pdf", content=b"%PDF-1.7"))
    )
    upload_api.validate_upload(
        _as_upload(
            FakeUploadFile(
                filename="spec.docx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                content=b"PK\x03\x04",
            )
        )
    )


def test_read_upload_content_accepts_matching_signature_and_markdown_without_magic() -> None:
    assert (
        _run(
            upload_api.read_upload_content(
                _as_upload(FakeUploadFile(filename="demo.pdf", content=b"%PDF-1.7")),
                max_upload_size_bytes=10,
                file_size_exceeded_detail="文件大小超过限制 (50MB)",
                content_extension_mismatch_detail="文件内容与扩展名不匹配: {extension}",
            )
        )
        == b"%PDF-1.7"
    )
    # Markdown 无魔数：任意文本内容可通过内容签名校验
    assert (
        _run(
            upload_api.read_upload_content(
                _as_upload(
                    FakeUploadFile(
                        filename="notes.md",
                        content_type="text/markdown",
                        content=b"# title\nbody",
                    )
                ),
                max_upload_size_bytes=64,
                file_size_exceeded_detail="文件大小超过限制 (50MB)",
                content_extension_mismatch_detail="文件内容与扩展名不匹配: {extension}",
            )
        )
        == b"# title\nbody"
    )


def test_read_upload_content_rejects_oversize_and_signature_mismatch() -> None:
    with pytest.raises(HTTPException) as oversize_exc:
        _run(
            upload_api.read_upload_content(
                _as_upload(FakeUploadFile(content=b"12345")),
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
                _as_upload(FakeUploadFile(content=b"PK\x03\x04")),
                max_upload_size_bytes=4,
                file_size_exceeded_detail="文件大小超过限制 (50MB)",
                content_extension_mismatch_detail="文件内容与扩展名不匹配: {extension}",
            )
        )
    assert mismatch_exc.value.status_code == 400
    assert mismatch_exc.value.detail == "文件内容与扩展名不匹配: .pdf"


def test_store_upload_writes_file_and_returns_stable_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeUUIDModule:
        NAMESPACE_DNS = object()

        @staticmethod
        def uuid5(namespace: object, value: object) -> str:
            _ = namespace, value
            return "user-uuid"

    monkeypatch.setattr(upload_api, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(upload_api, "uuid", FakeUUIDModule)
    monkeypatch.setattr(upload_api, "datetime", FakeDateTime)

    file_info = _run(upload_api._store_upload(_as_upload(FakeUploadFile(filename="manual.pdf")), 3))

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
    assert (
        tmp_path / "user-uuid" / "20260102_030405" / "manual_20260102_030405.pdf"
    ).read_bytes() == b"%PDF-1.7"


def test_process_upload_runs_validation_storage_and_task_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = FakeUploadFile("guide.pdf")
    manager = FakeTaskManager(task_id="task-9")
    file_info = {"path": "uploads/guide.pdf", "filename": "guide.pdf", "user_id": 7}
    captured: list[tuple[str, object]] = []

    def fake_validate_upload(upload_file: UploadFile) -> None:
        captured.append(("validate", upload_file))

    async def fake_store_upload(upload_file: UploadFile, user_id: int) -> dict[str, object]:
        captured.append(("store", (upload_file, user_id)))
        return file_info

    async def fake_get_task_manager() -> FakeTaskManager:
        return manager

    monkeypatch.setattr(upload_api, "validate_upload", fake_validate_upload)
    monkeypatch.setattr(upload_api, "_store_upload", fake_store_upload)
    monkeypatch.setattr(upload_api, "get_task_manager", fake_get_task_manager)

    response = _run(upload_api.upload_file(_as_upload(file), 7))

    assert captured == [
        ("validate", _as_upload(file)),
        ("store", (_as_upload(file), 7)),
    ]
    assert len(manager.submit_calls) == 1
    submitted_func, submitted_args = manager.submit_calls[0]
    # process_file 是绑定方法，校验所属类与方法名
    assert getattr(submitted_func, "__self__", None).__class__ is upload_api.IndexingService
    assert getattr(submitted_func, "__name__", "") == "process_file"
    assert submitted_args == (file_info,)
    assert response == {
        **file_info,
        "task_id": "task-9",
        "message": "文件已上传，后台正在解析索引。请通过 task_id 查询进度。",
    }


def test_get_upload_status_or_raise_returns_status_and_raises_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_task_manager_ok() -> FakeTaskManager:
        return FakeTaskManager(status={"status": "running"})

    monkeypatch.setattr(upload_api, "get_task_manager", fake_get_task_manager_ok)
    assert _run(upload_api.get_upload_status("task-ok")) == {"status": "running"}

    async def fake_get_task_manager_missing() -> FakeTaskManager:
        return FakeTaskManager(status=None)

    monkeypatch.setattr(upload_api, "get_task_manager", fake_get_task_manager_missing)
    with pytest.raises(HTTPException) as exc:
        _run(upload_api.get_upload_status("task-missing"))

    assert exc.value.status_code == 404
    assert exc.value.detail == "任务不存在: task-missing"
