"""文档上传接口。

这个模块只处理上传入口和任务提交，不承担文档索引细节。
通过把上传校验、落盘和响应构造拆到 `upload_support.py`，
可以降低 `upload_file()` 的阅读负担。
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.common import run_api_action
from app.api.upload_storage_support import StoredUploadFileInfo, store_upload
from app.api.upload_support import (
    TASK_NOT_FOUND_DETAIL,
    UploadAcceptedResponse,
    build_upload_accepted_response,
    validate_upload,
)
from app.core.logger import get_logger
from app.services.indexing_service import (
    IndexingService,
)
from app.services.task_queue import TaskStatusPayload, get_task_manager

logger = get_logger(__name__)

router = APIRouter(tags=["upload"])


async def _submit_upload_task(file_info: StoredUploadFileInfo) -> str:
    """提交后台索引任务并返回 task_id。"""
    task_manager = await get_task_manager()
    return await task_manager.submit(IndexingService().process_file, file_info)


async def _process_upload(file: UploadFile, user_id: int) -> UploadAcceptedResponse:
    """串联上传校验、落盘和任务提交。"""
    validate_upload(file)
    file_info = await store_upload(file, user_id)
    task_id = await _submit_upload_task(file_info)
    return build_upload_accepted_response(file_info, task_id)


async def _get_upload_status_or_raise(task_id: str) -> TaskStatusPayload:
    """读取任务状态，不存在时抛出 404。"""
    task_manager = await get_task_manager()
    status = await task_manager.get_status(task_id)
    if status is None:
        raise HTTPException(
            status_code=404,
            detail=TASK_NOT_FOUND_DETAIL.format(task_id=task_id),
        )
    return status


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: int = Form(...),
) -> UploadAcceptedResponse:
    """上传文档并异步解析索引。"""
    return await run_api_action(
        "upload_file",
        _process_upload(file, user_id),
        logger=logger,
        user_id=user_id,
        filename=file.filename,
    )


@router.get("/upload/status/{task_id}")
async def get_upload_status(task_id: str) -> TaskStatusPayload:
    """查询文档解析任务状态。"""
    return await run_api_action(
        "get_upload_status",
        _get_upload_status_or_raise(task_id),
        logger=logger,
        task_id=task_id,
    )
