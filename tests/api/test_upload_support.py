import pytest
from fastapi import HTTPException

import app.api.upload as upload_api


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
                content=b"%PDF-1.7",
            )
        )
    assert missing_type_exc.value.status_code == 400
    assert missing_type_exc.value.detail == "无法识别文件类型"


def test_build_upload_accepted_response_returns_stable_shape() -> None:
    file = FakeUploadFile(
        filename="guide.pdf",
        content_type="application/pdf",
        content=b"%PDF-1.7",
    )
    file_info = {
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
    assert upload_api.build_upload_accepted_response(file_info, "task-1") == {
        **file_info,
        "task_id": "task-1",
        "message": "文件已上传，后台正在解析索引。请通过 task_id 查询进度。",
    }
