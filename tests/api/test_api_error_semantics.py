"""API 异常映射与文档删除接口的语义测试。"""

from __future__ import annotations

import logging

import pytest
from app.api.common import run_api_action
from app.shared.core.errors import ResourceNotFoundError
from fastapi import HTTPException


async def test_resource_not_found_maps_to_404() -> None:
    """业务层"不存在" → 404，不再被统一包装成 500。

    回归背景：Repository 曾抛裸 ValueError，被 run_api_action 翻成
    500 Internal Server Error —— 前端无法区分"资源已没了"和"服务器坏了"。
    """

    async def operation() -> None:
        raise ResourceNotFoundError("会话不存在或不属于当前用户")

    with pytest.raises(HTTPException) as exc_info:
        await run_api_action("demo", operation(), logger=logging.getLogger("t"))

    assert exc_info.value.status_code == 404
    assert "不存在" in str(exc_info.value.detail)


async def test_unexpected_error_still_maps_to_500() -> None:
    async def operation() -> None:
        raise RuntimeError("boom")

    with pytest.raises(HTTPException) as exc_info:
        await run_api_action("demo", operation(), logger=logging.getLogger("t"))

    assert exc_info.value.status_code == 500


async def test_http_exception_passes_through_unchanged() -> None:
    async def operation() -> None:
        raise HTTPException(status_code=400, detail="bad input")

    with pytest.raises(HTTPException) as exc_info:
        await run_api_action("demo", operation(), logger=logging.getLogger("t"))

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------- #
# DELETE /api/documents/user/{user_id}/{doc_id}
# ---------------------------------------------------------------------- #


class _FakeRepo:
    def __init__(self, row):
        self._row = row
        self.delete_calls: list[tuple[int, str]] = []

    async def delete_owned(self, user_id: int, doc_id: str):
        self.delete_calls.append((user_id, doc_id))
        return self._row


class _FakeSessionCtx:
    def __init__(self) -> None:
        pass

    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc) -> None:
        return None


async def test_delete_document_soft_deletes_chunks(monkeypatch) -> None:
    import app.knowledge.application.document_service as ds_module
    import app.knowledge.infrastructure.doc_parser.retrieval.hybrid_search as hs_module

    class _Row:
        doc_id = "doc_ok"

    fake_repo = _FakeRepo(_Row())
    monkeypatch.setattr(ds_module, "UserDocumentRepository", lambda _db: fake_repo)

    soft_deleted_docs: list[str] = []

    class _FakeSearcher:
        async def soft_delete_document(self, doc_id: str):
            soft_deleted_docs.append(doc_id)
            return {"soft_deleted": 4, "max_version": 2}

    monkeypatch.setattr(hs_module, "get_shared_searcher", lambda: _FakeSearcher())

    service = ds_module.DocumentService(session_factory=_FakeSessionCtx)
    result = await service.delete_document(7, "doc_ok")

    assert fake_repo.delete_calls == [(7, "doc_ok")]
    assert soft_deleted_docs == ["doc_ok"]
    assert result == {"doc_id": "doc_ok", "soft_deleted_chunks": 4}


async def test_delete_document_raises_not_found_for_foreign_doc(monkeypatch) -> None:
    """归属不符 → ResourceNotFoundError，且绝不触发 Milvus 软删。"""
    import app.knowledge.application.document_service as ds_module
    import app.knowledge.infrastructure.doc_parser.retrieval.hybrid_search as hs_module

    fake_repo = _FakeRepo(None)
    monkeypatch.setattr(ds_module, "UserDocumentRepository", lambda _db: fake_repo)

    searcher_touched: list[str] = []

    class _FakeSearcher:
        async def soft_delete_document(self, doc_id: str):
            searcher_touched.append(doc_id)
            return {}

    monkeypatch.setattr(hs_module, "get_shared_searcher", lambda: _FakeSearcher())

    service = ds_module.DocumentService(session_factory=_FakeSessionCtx)
    with pytest.raises(ResourceNotFoundError):
        await service.delete_document(7, "doc_foreign")

    assert searcher_touched == []


async def test_delete_document_survives_chunk_soft_delete_failure(monkeypatch) -> None:
    """chunk 软删失败不回滚：元信息已删，残留 chunk 由 is_deleted 过滤兜底。"""
    import app.knowledge.application.document_service as ds_module
    import app.knowledge.infrastructure.doc_parser.retrieval.hybrid_search as hs_module

    class _Row:
        doc_id = "doc_ok"

    monkeypatch.setattr(ds_module, "UserDocumentRepository", lambda _db: _FakeRepo(_Row()))

    class _BrokenSearcher:
        async def soft_delete_document(self, doc_id: str):
            raise RuntimeError("milvus down")

    monkeypatch.setattr(hs_module, "get_shared_searcher", lambda: _BrokenSearcher())

    service = ds_module.DocumentService(session_factory=_FakeSessionCtx)
    result = await service.delete_document(7, "doc_ok")

    assert result == {"doc_id": "doc_ok", "soft_deleted_chunks": 0}
