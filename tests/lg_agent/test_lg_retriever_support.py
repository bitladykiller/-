from app.lg_agent.lg_retriever_support import (
    build_milvus_doc_fallback_record,
    build_milvus_doc_record,
    normalize_retriever_result,
)


def test_normalize_retriever_result_supports_records_and_cyphers() -> None:
    record_payload = normalize_retriever_result(
        {
            "records": {"name": "alice"},
            "errors": ["warn"],
            "steps": ["kg"],
        },
        task="查用户",
    )
    cypher_payload = normalize_retriever_result(
        {
            "cyphers": [
                {"records": [{"id": 1}]},
                {"records": {"id": 2}},
            ]
        },
        task="查订单",
    )

    assert record_payload == {
        "task": "查用户",
        "records": [{"name": "alice"}],
        "errors": ["warn"],
        "steps": ["kg"],
        "raw": {
            "records": {"name": "alice"},
            "errors": ["warn"],
            "steps": ["kg"],
        },
    }
    assert cypher_payload["records"] == [{"id": 1}, {"id": 2}]


def test_build_milvus_doc_record_and_fallback_record_are_stable() -> None:
    record = build_milvus_doc_record(
        {
            "chunk_type": "text",
            "section_path": "章节-1",
            "source_file": "doc-1.md",
            "raw_text": "内容-1",
            "rrf_score": 0.1,
            "rerank_score": 0.2,
        }
    )

    assert record == {
        "chunk_type": "text",
        "section_path": "章节-1",
        "source_file": "doc-1.md",
        "raw_text": "内容-1",
        "rrf_score": 0.1,
        "rerank_score": 0.2,
    }
    assert build_milvus_doc_fallback_record("文档检索暂时不可用。") == [
        {"message": "文档检索暂时不可用。"}
    ]
