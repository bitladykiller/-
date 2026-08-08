"""UserDocumentRepository 单测：租户强制过滤。"""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.knowledge.infrastructure.repository.user_document_repository import (
    UserDocumentRepository,
)


class FakeDoc:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 1)
        self.tenant_id = kwargs.get("tenant_id", "default")
        self.user_id = kwargs.get("user_id", 0)
        self.doc_id = kwargs.get("doc_id", "")
        self.title = kwargs.get("title", "")
        self.original_name = kwargs.get("original_name", "")
        self.source_path = kwargs.get("source_path", "")
        self.content_hash = kwargs.get("content_hash", "")
        self.status = kwargs.get("status", "pending")
        self.version = kwargs.get("version", 0)
        self.chunk_count = kwargs.get("chunk_count", 0)
        self.last_task_id = kwargs.get("last_task_id", "")
        self.error_message = kwargs.get("error_message", "")
        self.created_at = kwargs.get("created_at", datetime(2026, 7, 1))
        self.updated_at = kwargs.get("updated_at", datetime(2026, 7, 1))


class FakeResult:
    def __init__(self, rows=None, scalar=None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class FakeSession:
    def __init__(self, rows=None, scalar=None) -> None:
        self.rows = rows
        self.scalar = scalar
        self.committed = False
        self.added: list = []
        self.deleted: list = []
        self.refreshed: list = []
        self.calls: list[str] = []

    async def execute(self, stmt, params=None):
        self.calls.append(str(stmt))
        return FakeResult(self.rows, self.scalar)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def delete(self, obj) -> None:
        self.deleted.append(obj)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, obj) -> None:
        self.refreshed.append(obj)


def _run(awaitable):
    return asyncio.run(awaitable)


def test_create_stamps_tenant() -> None:
    session = FakeSession()
    repo = UserDocumentRepository(session)

    row = _run(
        repo.create(
            tenant_id="t_1",
            user_id=7,
            doc_id="doc_x",
            title="x.md",
            original_name="x.md",
            source_path="/tmp/x.md",
            content_hash="h",
        )
    )

    assert row.tenant_id == "t_1"
    assert row.user_id == 7
    assert session.added[0].tenant_id == "t_1"


def test_get_by_doc_id_filters_tenant() -> None:
    row = FakeDoc(tenant_id="t_1", doc_id="doc_x")
    session = FakeSession(scalar=row)
    repo = UserDocumentRepository(session)

    result = _run(repo.get_by_doc_id("t_1", "doc_x"))

    assert result is row
    assert "user_documents.tenant_id" in session.calls[0]


def test_get_owned_requires_tenant_and_user() -> None:
    session = FakeSession(scalar=None)
    repo = UserDocumentRepository(session)

    # 归属不符（另一租户的 doc_id）→ None
    assert _run(repo.get_owned("t_2", 7, "doc_x")) is None
    sql = session.calls[0]
    assert "user_documents.tenant_id" in sql
    assert "user_documents.user_id" in sql
    assert "user_documents.doc_id" in sql


def test_list_by_user_filters_tenant() -> None:
    rows = [FakeDoc(tenant_id="t_1", user_id=7, doc_id="a")]
    session = FakeSession(rows=rows)
    repo = UserDocumentRepository(session)

    result = _run(repo.list_by_user("t_1", 7))

    assert len(result) == 1
    assert "user_documents.tenant_id" in session.calls[0]


def test_delete_owned_only_deletes_matching_row() -> None:
    row = FakeDoc(tenant_id="t_1", user_id=7, doc_id="doc_x")
    session = FakeSession(scalar=row)
    repo = UserDocumentRepository(session)

    deleted = _run(repo.delete_owned("t_1", 7, "doc_x"))

    assert deleted is row
    assert session.deleted == [row]
    assert session.committed is True


def test_mark_indexing_and_apply_result() -> None:
    session = FakeSession()
    repo = UserDocumentRepository(session)
    row = FakeDoc(tenant_id="t_1", doc_id="doc_x")

    _run(
        repo.mark_indexing(
            row,
            source_path="/tmp/new.md",
            content_hash="h2",
            original_name="new.md",
            last_task_id="task-1",
        )
    )
    assert row.status == "indexing"
    assert row.last_task_id == "task-1"

    _run(
        repo.apply_index_result(
            row,
            status="ready",
            version=2,
            chunk_count=5,
            last_task_id="task-1",
        )
    )
    assert row.status == "ready"
    assert row.version == 2
    assert row.chunk_count == 5


def test_to_summary_includes_tenant() -> None:
    row = FakeDoc(tenant_id="t_1", user_id=7, doc_id="doc_x", title="x.md")

    summary = UserDocumentRepository.to_summary(row)

    assert summary["tenant_id"] == "t_1"
    assert summary["doc_id"] == "doc_x"
