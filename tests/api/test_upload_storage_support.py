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
        *,
        filename: str | None,
        content_type: str | None,
        content: bytes,
    ) -> None:
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self) -> bytes:
        return self._content


def _run(awaitable):
    return asyncio.run(awaitable)


def test_validate_magic_bytes_respects_signature_table() -> None:
    assert upload_api._validate_magic_bytes("demo.pdf", b"%PDF-1.7") is True
    assert upload_api._validate_magic_bytes("demo.pdf", b"PK\x03\x04") is False
    assert upload_api._validate_magic_bytes("demo.unknown", b"whatever") is True


def test_build_upload_target_uses_deterministic_directory_shape(tmp_path: Path) -> None:
    class FakeUUIDModule:
        NAMESPACE_DNS = object()

        @staticmethod
        def uuid5(namespace, value):
            return "user-uuid"

    target = upload_api.build_upload_target(
        7,
        "manual.pdf",
        upload_dir_root=tmp_path,
        uuid_module=FakeUUIDModule,
        clock=FakeDateTime,
    )

    assert target == {
        "user_uuid": "user-uuid",
        "timestamp": "20260102_030405",
        "upload_dir": tmp_path / "user-uuid" / "20260102_030405",
        "file_path": tmp_path / "user-uuid" / "20260102_030405" / "manual_20260102_030405.pdf",
    }
    assert target["upload_dir"].exists()


def test_build_file_info_returns_stable_shape() -> None:
    file = FakeUploadFile(
        filename="guide.pdf",
        content_type="application/pdf",
        content=b"%PDF-1.7",
    )
    file_info = upload_api.build_file_info(
        file=file,
        user_id=3,
        user_uuid="user-uuid",
        timestamp="20260102_030405",
        file_path=Path("uploads/user-uuid/20260102_030405/guide.pdf"),
        directory=Path("uploads/user-uuid/20260102_030405"),
        size=1024,
    )

    assert file_info == {
        "filename": "guide.pdf",
        "original_name": "guide.pdf",
        "size": 1024,
        "type": "application/pdf",
        "path": "uploads/user-uuid/20260102_030405/guide.pdf",
        "user_id": 3,
        "user_uuid": "user-uuid",
        "upload_time": "20260102_030405",
        "directory": "uploads/user-uuid/20260102_030405",
    }


def test_read_upload_content_rejects_oversize_and_signature_mismatch() -> None:
    with pytest.raises(HTTPException) as oversize_exc:
        _run(
            upload_api.read_upload_content(
                FakeUploadFile(
                    filename="demo.pdf",
                    content_type="application/pdf",
                    content=b"12345",
                ),
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
                FakeUploadFile(
                    filename="demo.pdf",
                    content_type="application/pdf",
                    content=b"PK\x03\x04",
                ),
                max_upload_size_bytes=4,
                file_size_exceeded_detail="文件大小超过限制 (50MB)",
                content_extension_mismatch_detail="文件内容与扩展名不匹配: {extension}",
            )
        )
    assert mismatch_exc.value.status_code == 400
    assert mismatch_exc.value.detail == "文件内容与扩展名不匹配: .pdf"
