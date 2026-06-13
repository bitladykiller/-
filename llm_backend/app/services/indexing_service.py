"""文档索引服务。

上传后的文件通过 `rag_doc_parser` 解析，再写入 Milvus / BM25 检索索引。
本文件只保留“校验输入文件 + 调用解析索引管道”这一层，不承载上传或任务编排逻辑。
"""
from __future__ import annotations

from pathlib import Path

from app.services.indexing_support import (
    FILE_NOT_FOUND_MESSAGE,
    ChunkIndexer,
    DocIDFactory,
    IndexingResult,
    ParsedChunks,
    PipelineLoader,
    STATUS_ERROR,
    STATUS_SUCCESS,
    UploadFileInfo,
    build_doc_id,
    build_empty_document_result,
    build_missing_dependency_result,
    build_result,
    load_pipeline_dependencies,
    resolve_source_or_error,
)


class IndexingService:
    """文档索引服务。"""

    def __init__(
        self,
        *,
        pipeline_loader: PipelineLoader | None = None,
        doc_id_factory: DocIDFactory | None = None,
    ) -> None:
        self._pipeline_loader = pipeline_loader or load_pipeline_dependencies
        self._doc_id_factory = doc_id_factory or build_doc_id

    async def _index_chunks(
        self,
        *,
        chunks: ParsedChunks,
        searcher: ChunkIndexer,
        doc_id: str,
        path: Path,
    ) -> IndexingResult:
        """把解析结果写入检索索引，并构造统一成功响应。"""
        if not chunks:
            return build_empty_document_result()

        count = await searcher.index(chunks)
        return build_result(
            STATUS_SUCCESS,
            chunks=count,
            doc_id=doc_id,
            source_file=str(path),
        )

    async def _parse_and_index(
        self,
        *,
        path: Path,
        user_id: int,
    ) -> IndexingResult:
        """执行“解析文档 -> 写入检索索引”的主流程。"""
        parse_document, searcher = self._pipeline_loader()
        doc_id = self._doc_id_factory(user_id)
        chunks = parse_document(str(path), doc_id=doc_id)
        return await self._index_chunks(
            chunks=chunks,
            searcher=searcher,
            doc_id=doc_id,
            path=path,
        )

    async def process_file(self, file_info: UploadFileInfo) -> IndexingResult:
        """处理上传文件并写入检索索引。"""
        source, validation_error = resolve_source_or_error(file_info)
        if validation_error is not None:
            return validation_error

        # defensive fallback：理论上经过 resolve_source_or_error 后这里不会为 None。
        if source is None:
            return build_result(STATUS_ERROR, message=FILE_NOT_FOUND_MESSAGE)
        path = source["path"]
        user_id = source["user_id"]

        try:
            return await self._parse_and_index(path=path, user_id=user_id)
        except ImportError:
            return build_missing_dependency_result(file_info)
        except Exception as exc:
            return build_result(STATUS_ERROR, message=str(exc))


__all__ = ["IndexingResult", "IndexingService", "UploadFileInfo"]
