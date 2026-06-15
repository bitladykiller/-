import asyncio
import builtins
import sys
import types
from pathlib import Path

from app.knowledge.application.indexing_service import process_file


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


def _install_fake_pipeline(
    monkeypatch,
    *,
    parse_document,
    indexer: FakeChunkIndexer,
) -> None:
    rag_doc_parser_pkg = types.ModuleType("rag_doc_parser")
    retrieval_pkg = types.ModuleType("rag_doc_parser.retrieval")

    pipeline_module = types.ModuleType("rag_doc_parser.pipeline")
    pipeline_module.parse_document = parse_document

    config_module = types.ModuleType("rag_doc_parser.retrieval.config")

    class FakeRetrievalConfig:
        pass

    config_module.RetrievalConfig = FakeRetrievalConfig

    hybrid_module = types.ModuleType("rag_doc_parser.retrieval.hybrid_search")

    class FakeHybridSearcher:
        def __init__(self, _config) -> None:
            self._indexer = indexer

        async def index(self, chunks: list[dict]) -> int:
            return await self._indexer.index(chunks)

    hybrid_module.HybridSearcher = FakeHybridSearcher

    monkeypatch.setitem(sys.modules, "rag_doc_parser", rag_doc_parser_pkg)
    monkeypatch.setitem(sys.modules, "rag_doc_parser.retrieval", retrieval_pkg)
    monkeypatch.setitem(sys.modules, "rag_doc_parser.pipeline", pipeline_module)
    monkeypatch.setitem(sys.modules, "rag_doc_parser.retrieval.config", config_module)
    monkeypatch.setitem(sys.modules, "rag_doc_parser.retrieval.hybrid_search", hybrid_module)


def test_process_file_returns_error_for_missing_source() -> None:
    result = _run(process_file({"path": "", "user_id": 1}))

    assert result == {"status": "error", "message": "文件不存在"}


def test_process_file_returns_error_for_unsupported_extension(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.txt"
    file_path.write_text("hello", encoding="utf-8")

    result = _run(process_file({"path": str(file_path), "user_id": 1}))

    assert result == {"status": "error", "message": "不支持的文件类型: .txt"}


def test_process_file_returns_warning_when_dependency_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "demo.pdf"
    file_path.write_bytes(b"%PDF-1.7")

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("rag_doc_parser"):
            raise ImportError("rag_doc_parser missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    file_info = {"path": str(file_path), "user_id": 3}
    result = _run(process_file(file_info))

    assert result == {
        "status": "warning",
        "message": "rag_doc_parser 模块未安装，文档已保存但未索引",
        "file_info": file_info,
    }


def test_process_file_returns_empty_document_result(monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "demo.pdf"
    file_path.write_bytes(b"%PDF-1.7")

    indexer = FakeChunkIndexer(result_count=0)
    _install_fake_pipeline(
        monkeypatch,
        parse_document=lambda path, *, doc_id: [],
        indexer=indexer,
    )

    result = _run(
        process_file({"path": str(file_path), "user_id": 8})
    )

    assert result == {
        "status": "success",
        "chunks": 0,
        "message": "文档无有效内容",
    }


def test_process_file_indexes_parsed_chunks(monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "demo.docx"
    file_path.write_bytes(b"PK\x03\x04demo")

    chunks = [{"content": "chunk-1"}, {"content": "chunk-2"}]
    indexer = FakeChunkIndexer(result_count=2)
    captured_doc_ids: list[str] = []
    _install_fake_pipeline(
        monkeypatch,
        parse_document=lambda path, *, doc_id: captured_doc_ids.append(doc_id) or chunks,
        indexer=indexer,
    )

    result = _run(
        process_file({"path": f"  {file_path}  ", "user_id": "9"})
    )

    assert indexer.indexed_chunks == chunks
    assert captured_doc_ids == [result["doc_id"]]
    assert result == {
        "status": "success",
        "chunks": 2,
        "doc_id": captured_doc_ids[0],
        "source_file": str(file_path),
    }
    assert captured_doc_ids[0].startswith("upload_9_")
    assert len(captured_doc_ids[0]) == len("upload_9_") + 8


def test_process_file_generates_default_doc_id(monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "demo.pdf"
    file_path.write_bytes(b"%PDF-1.7")

    indexer = FakeChunkIndexer(result_count=1)
    captured_doc_ids: list[str] = []
    _install_fake_pipeline(
        monkeypatch,
        parse_document=lambda path, *, doc_id: captured_doc_ids.append(doc_id) or [{"content": path}],
        indexer=indexer,
    )

    result = _run(
        process_file({"path": f" {file_path} ", "user_id": "5"})
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
