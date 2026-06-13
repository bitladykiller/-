import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.api.upload_storage_support as upload_storage_support


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
    assert upload_storage_support.validate_magic_bytes("demo.pdf", b"%PDF-1.7") is True
    assert upload_storage_support.validate_magic_bytes("demo.pdf", b"PK\x03\x04") is False
    assert upload_storage_support.validate_magic_bytes("demo.unknown", b"whatever") is True


def test_build_upload_target_uses_deterministic_directory_shape(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(upload_storage_support, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(
        upload_storage_support.uuid,
        "uuid5",
        lambda namespace, value: "user-uuid",
    )
    monkeypatch.setattr(upload_storage_support, "datetime", FakeDateTime)

    target = upload_storage_support.build_upload_target(7, "manual.pdf")

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
    file_info = upload_storage_support.build_file_info(
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


def test_read_upload_content_rejects_oversize_and_signature_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(upload_storage_support, "MAX_UPLOAD_SIZE_BYTES", 4)

    with pytest.raises(HTTPException) as oversize_exc:
        _run(
            upload_storage_support.read_upload_content(
                FakeUploadFile(
                    filename="demo.pdf",
                    content_type="application/pdf",
                    content=b"12345",
                )
            )
        )
    assert oversize_exc.value.status_code == 400
    assert oversize_exc.value.detail == upload_storage_support.FILE_SIZE_EXCEEDED_DETAIL

    with pytest.raises(HTTPException) as mismatch_exc:
        _run(
            upload_storage_support.read_upload_content(
                FakeUploadFile(
                    filename="demo.pdf",
                    content_type="application/pdf",
                    content=b"PK\x03\x04",
                )
            )
        )
    assert mismatch_exc.value.status_code == 400
    assert mismatch_exc.value.detail == "文件内容与扩展名不匹配: .pdf"


def test_store_upload_writes_file_and_returns_metadata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(upload_storage_support, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(
        upload_storage_support.uuid,
        "uuid5",
        lambda namespace, value: "user-uuid",
    )
    monkeypatch.setattr(upload_storage_support, "datetime", FakeDateTime)

    file = FakeUploadFile(
        filename="guide.pdf",
        content_type="application/pdf",
        content=b"%PDF-1.7 demo",
    )

    file_info = _run(upload_storage_support.store_upload(file, 11))

    saved_path = tmp_path / "user-uuid" / "20260102_030405" / "guide_20260102_030405.pdf"
    assert saved_path.read_bytes() == b"%PDF-1.7 demo"
    assert file_info["path"] == saved_path.as_posix()
    assert file_info["directory"] == str(saved_path.parent)
    assert file_info["size"] == len(b"%PDF-1.7 demo")
