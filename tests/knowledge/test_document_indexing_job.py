"""文档索引任务单测：MySQL 回写必须带租户。"""

from __future__ import annotations

import asyncio

from app.knowledge.application.document_indexing_job import (
    run_document_indexing_job,
    run_document_indexing_job_with_task,
)


class FakeIndexingService:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.file_info: dict | None = None

    async def process_file(self, file_info: dict) -> dict:
        self.file_info = dict(file_info)
        return dict(self.result)


class FakeDocumentService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def apply_indexing_result(
        self,
        *,
        tenant_id: str,
        doc_id: str,
        indexing_result: dict,
        task_id: str = "",
    ) -> None:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "doc_id": doc_id,
                "indexing_result": indexing_result,
                "task_id": task_id,
            }
        )


def _run(awaitable):
    return asyncio.run(awaitable)


def test_run_document_indexing_job_passes_tenant_to_mysql(monkeypatch) -> None:
    import app.knowledge.application.document_indexing_job as job_module

    service = FakeIndexingService(
        {"status": "success", "chunks": 3, "version": 1, "doc_id": "doc_1"}
    )
    document_service = FakeDocumentService()
    monkeypatch.setattr(job_module, "IndexingService", lambda: service)
    monkeypatch.setattr(job_module, "document_service", document_service)

    result = _run(
        run_document_indexing_job(
            {
                "path": "/tmp/a.md",
                "user_id": 7,
                "tenant_id": "t_1",
                "doc_id": "doc_1",
            },
            task_id="task-9",
        )
    )

    assert result["status"] == "success"
    assert document_service.calls == [
        {
            "tenant_id": "t_1",
            "doc_id": "doc_1",
            "indexing_result": result,
            "task_id": "task-9",
        }
    ]


def test_run_document_indexing_job_skips_when_no_doc_id(monkeypatch) -> None:
    import app.knowledge.application.document_indexing_job as job_module

    service = FakeIndexingService({"status": "error", "message": "解析失败"})
    document_service = FakeDocumentService()
    monkeypatch.setattr(job_module, "IndexingService", lambda: service)
    monkeypatch.setattr(job_module, "document_service", document_service)

    result = _run(
        run_document_indexing_job({"path": "/tmp/a.md", "user_id": 7, "tenant_id": "t_1"})
    )

    assert result["status"] == "error"
    assert document_service.calls == []


def test_run_document_indexing_job_with_task_forwards_file_info(monkeypatch) -> None:
    import app.knowledge.application.document_indexing_job as job_module

    service = FakeIndexingService({"status": "success", "chunks": 1, "doc_id": "d"})
    document_service = FakeDocumentService()
    monkeypatch.setattr(job_module, "IndexingService", lambda: service)
    monkeypatch.setattr(job_module, "document_service", document_service)

    result = _run(
        run_document_indexing_job_with_task(
            {"path": "/tmp/a.md", "user_id": 7, "tenant_id": "t_1", "doc_id": "d"},
            "task-1",
        )
    )

    assert result["status"] == "success"
    assert document_service.calls[0]["task_id"] == "task-1"
    assert document_service.calls[0]["tenant_id"] == "t_1"
