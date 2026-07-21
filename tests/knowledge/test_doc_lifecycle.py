"""RAG 文档生命周期纯函数测试。"""

import pytest
from app.knowledge.infrastructure.doc_parser.retrieval.doc_lifecycle import (
    ACTIVE_FILTER,
    build_soft_delete_record,
    doc_id_filter,
    escape_milvus_string,
    hard_purge_filter,
    merge_active_filter,
    next_version,
    validate_doc_id,
)


def test_validate_doc_id_accepts_stable_ids() -> None:
    assert validate_doc_id("kb_user.1:faq-v2") == "kb_user.1:faq-v2"
    assert validate_doc_id("  upload_1_abcd1234  ") == "upload_1_abcd1234"


def test_validate_doc_id_rejects_injection() -> None:
    with pytest.raises(ValueError):
        validate_doc_id('evil" or true')
    with pytest.raises(ValueError):
        validate_doc_id("")


def test_escape_and_doc_id_filter() -> None:
    assert escape_milvus_string('a"b\\c') == 'a\\"b\\\\c'
    assert doc_id_filter("doc_1") == 'doc_id == "doc_1"'
    assert doc_id_filter("doc_1", active_only=True) == (
        '(doc_id == "doc_1") and (is_deleted == false)'
    )


def test_merge_active_filter() -> None:
    assert merge_active_filter(None) == ACTIVE_FILTER
    assert merge_active_filter("chunk_type == \"table\"") == (
        '(is_deleted == false) and (chunk_type == "table")'
    )


def test_next_version_and_soft_delete_record() -> None:
    assert next_version(None) == 1
    assert next_version(0) == 1
    assert next_version(3) == 4
    record = build_soft_delete_record("c1", updated_at=100)
    assert record == {"chunk_id": "c1", "is_deleted": True, "updated_at": 100}


def test_hard_purge_filter() -> None:
    assert hard_purge_filter(cutoff_ts=99) == "is_deleted == true and updated_at < 99"
