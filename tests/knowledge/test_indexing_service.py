import asyncio
from pathlib import Path

from app.knowledge.application.indexing_service import IndexingService


class FakeChunkIndexer:
    def __init__(self, result_count: int) -> None:
        self._result_count = result_count
        self.indexed_chunks: list[dict] = []

    async def index(self, chunks: list[dict]) -> int:
        self.indexed_chunks = chunks
        return self._result_count


def _run(awaitable):
    """统一执行服务层异步调用，减少测试样板。"""
    return asyncio.run(awaitable)


def test_process_file_returns_error_for_missing_source() -> None:
    service = IndexingService()
    result = _run(service.process_file({"path": "", "user_id": 1}))

    assert result == {"status": "error", "message": "文件不存在"}


def test_process_file_returns_error_for_unsupported_extension(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.txt"
    file_path.write_text("hello", encoding="utf-8")

    service = IndexingService()
    result = _run(
        service.process_file({"path": str(file_path), "user_id": 1})
    )

    assert result == {"status": "error", "message": "不支持的文件类型: .txt"}


def test_process_file_returns_warning_when_dependency_missing(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.pdf"
    file_path.write_bytes(b"%PDF-1.7")

    def raise_import_error():
        raise ImportError("app.knowledge.infrastructure.doc_parser missing")

    service = IndexingService(pipeline_loader=raise_import_error)
    file_info = {"path": str(file_path), "user_id": 3}
    result = _run(service.process_file(file_info))

    assert result == {
        "status": "warning",
        "message": "app.knowledge.infrastructure.doc_parser 模块未安装，文档已保存但未索引",
        "file_info": file_info,
    }


def test_process_file_returns_empty_document_result(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.pdf"
    file_path.write_bytes(b"%PDF-1.7")

    indexer = FakeChunkIndexer(result_count=0)

    def load_pipeline():
        return lambda path, *, doc_id: [], indexer

    service = IndexingService(
        pipeline_loader=load_pipeline,
        doc_id_factory=lambda user_id: f"doc-{user_id}",
    )
    result = _run(
        service.process_file({"path": str(file_path), "user_id": 8})
    )

    assert result == {
        "status": "success",
        "chunks": 0,
        "message": "文档无有效内容",
    }


def test_process_file_indexes_parsed_chunks(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.docx"
    file_path.write_bytes(b"PK\x03\x04demo")

    chunks = [{"content": "chunk-1"}, {"content": "chunk-2"}]
    indexer = FakeChunkIndexer(result_count=2)

    def load_pipeline():
        return lambda path, *, doc_id: chunks, indexer

    service = IndexingService(
        pipeline_loader=load_pipeline,
        doc_id_factory=lambda user_id: f"doc-{user_id}",
    )
    result = _run(
        service.process_file({"path": f"  {file_path}  ", "user_id": "9"})
    )

    assert indexer.indexed_chunks == chunks
    assert result == {
        "status": "success",
        "chunks": 2,
        "doc_id": "doc-9",
        "source_file": str(file_path),
    }


def test_process_file_uses_default_doc_id_factory(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.pdf"
    file_path.write_bytes(b"%PDF-1.7")

    indexer = FakeChunkIndexer(result_count=1)
    captured_doc_ids: list[str] = []

    def load_pipeline():
        def parse_document(path: str, *, doc_id: str):
            captured_doc_ids.append(doc_id)
            return [{"content": path}]

        return parse_document, indexer

    service = IndexingService(pipeline_loader=load_pipeline)
    result = _run(
        service.process_file({"path": f" {file_path} ", "user_id": "5"})
    )

    assert len(captured_doc_ids) == 1
    assert captured_doc_ids[0].startswith("upload_5_")
    assert len(captured_doc_ids[0]) == len("upload_5_") + 8
    assert result == {
        "status": "success",
        "chunks": 1,
        "doc_id": captured_doc_ids[0],
        "source_file": str(file_path),
    }
