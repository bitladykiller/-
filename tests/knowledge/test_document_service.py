"""DocumentService 单元测试（内存 Fake session）。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest
from app.knowledge.application.document_service import DocumentService
from app.knowledge.infrastructure.models.user_document import UserDocument


class _FakeRepoState:
    def __init__(self) -> None:
        self.rows: dict[str, UserDocument] = {}
        self.next_id = 1


STATE = _FakeRepoState()


class FakeSession:
    pass


@asynccontextmanager
async def fake_session_factory():
    yield FakeSession()


class FakeRepo:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> UserDocument:
        now = datetime(2026, 7, 21, 12, 0, 0)
        row = UserDocument(
            id=STATE.next_id,
            tenant_id=kwargs.get("tenant_id", "default"),
            user_id=kwargs["user_id"],
            doc_id=kwargs["doc_id"],
            title=kwargs["title"],
            original_name=kwargs["original_name"],
            source_path=kwargs["source_path"],
            content_hash=kwargs["content_hash"],
            status=kwargs.get("status", "pending"),
            version=0,
            chunk_count=0,
            last_task_id=kwargs.get("last_task_id", ""),
            error_message="",
            created_at=now,
            updated_at=now,
        )
        STATE.next_id += 1
        STATE.rows[row.doc_id] = row
        return row

    async def get_by_doc_id(self, tenant_id: str, doc_id: str) -> UserDocument | None:
        row = STATE.rows.get(doc_id)
        if row and row.tenant_id == tenant_id:
            return row
        return None

    async def get_owned(self, tenant_id: str, user_id: int, doc_id: str) -> UserDocument | None:
        row = STATE.rows.get(doc_id)
        if row and row.tenant_id == tenant_id and row.user_id == user_id:
            return row
        return None

    async def list_by_user(self, tenant_id: str, user_id: int) -> list[UserDocument]:
        return [r for r in STATE.rows.values() if r.tenant_id == tenant_id and r.user_id == user_id]

    async def mark_indexing(self, row: UserDocument, **kwargs: Any) -> UserDocument:
        row.status = "indexing"
        row.source_path = kwargs["source_path"]
        row.content_hash = kwargs["content_hash"]
        row.original_name = kwargs["original_name"]
        if kwargs.get("title") is not None:
            row.title = kwargs["title"]
        if kwargs.get("last_task_id"):
            row.last_task_id = kwargs["last_task_id"]
        return row

    async def apply_index_result(self, row: UserDocument, **kwargs: Any) -> UserDocument:
        row.status = kwargs["status"]
        if kwargs.get("version") is not None:
            row.version = kwargs["version"]
        if kwargs.get("chunk_count") is not None:
            row.chunk_count = kwargs["chunk_count"]
        row.error_message = kwargs.get("error_message") or ""
        if kwargs.get("last_task_id") is not None:
            row.last_task_id = kwargs["last_task_id"]
        return row

    @staticmethod
    def to_summary(row: UserDocument) -> dict[str, Any]:
        from app.knowledge.infrastructure.repository.user_document_repository import (
            UserDocumentRepository,
        )

        return UserDocumentRepository.to_summary(row)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch):
    STATE.rows.clear()
    STATE.next_id = 1
    monkeypatch.setattr(
        "app.knowledge.application.document_service.UserDocumentRepository",
        FakeRepo,
    )
    yield


def test_prepare_create_and_list() -> None:
    svc = DocumentService(session_factory=fake_session_factory)
    meta = _run(
        svc.prepare_create(
            tenant_id="t_1",
            user_id=1,
            title="faq.md",
            original_name="faq.md",
            source_path="/tmp/faq.md",
            content_hash="abc",
            doc_id="kb_faq_1",
        )
    )
    assert meta["doc_id"] == "kb_faq_1"
    assert meta["status"] == "pending"
    docs = _run(svc.list_user_documents("t_1", 1))
    assert len(docs) == 1
    assert docs[0]["title"] == "faq.md"


def test_prepare_replace_checks_owner() -> None:
    svc = DocumentService(session_factory=fake_session_factory)
    _run(
        svc.prepare_create(
            tenant_id="t_1",
            user_id=1,
            title="a.md",
            original_name="a.md",
            source_path="/a",
            content_hash="1",
            doc_id="doc_x",
        )
    )
    with pytest.raises(ValueError, match="不属于"):
        _run(
            svc.prepare_replace(
                tenant_id="t_1",
                user_id=2,
                doc_id="doc_x",
                original_name="b.md",
                source_path="/b",
                content_hash="2",
            )
        )
    meta = _run(
        svc.prepare_replace(
            tenant_id="t_1",
            user_id=1,
            doc_id="doc_x",
            original_name="b.md",
            source_path="/b",
            content_hash="2",
        )
    )
    assert meta["status"] == "indexing"
    assert meta["original_name"] == "b.md"


def test_prepare_replace_skips_when_hash_matches() -> None:
    svc = DocumentService(session_factory=fake_session_factory)
    _run(
        svc.prepare_create(
            tenant_id="t_1",
            user_id=1,
            title="a.md",
            original_name="a.md",
            source_path="/a",
            content_hash="samehash",
            doc_id="doc_hash",
        )
    )
    meta = _run(
        svc.prepare_replace(
            tenant_id="t_1",
            user_id=1,
            doc_id="doc_hash",
            original_name="a.md",
            source_path="/a2",
            content_hash="samehash",
        )
    )
    assert meta["unchanged"] is True
    assert meta["status"] == "pending"
    assert meta["content_hash"] == "samehash"

    changed = _run(
        svc.prepare_replace(
            tenant_id="t_1",
            user_id=1,
            doc_id="doc_hash",
            original_name="a2.md",
            source_path="/a3",
            content_hash="newhash",
        )
    )
    assert changed.get("unchanged") is False
    assert changed["status"] == "indexing"
    assert changed["content_hash"] == "newhash"


def test_apply_indexing_result_ready() -> None:
    svc = DocumentService(session_factory=fake_session_factory)
    _run(
        svc.prepare_create(
            tenant_id="t_1",
            user_id=1,
            title="a.md",
            original_name="a.md",
            source_path="/a",
            content_hash="1",
            doc_id="doc_y",
        )
    )
    _run(
        svc.apply_indexing_result(
            tenant_id="t_1",
            doc_id="doc_y",
            indexing_result={"status": "success", "version": 2, "chunks": 5},
            task_id="t1",
        )
    )
    row = _run(svc.get_user_document("t_1", 1, "doc_y"))
    assert row is not None
    assert row["status"] == "ready"
    assert row["version"] == 2
    assert row["chunk_count"] == 5
