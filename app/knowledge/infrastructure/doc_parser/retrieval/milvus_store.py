"""
Milvus 向量存储 — Collection 管理、软删版本更新与向量检索。

策略 2（软删除 + version）：
- Schema 含 is_deleted / version / updated_at / content_hash
- 检索默认过滤 is_deleted == false
- 文档更新：soft_delete_by_doc_id → insert 新 version
- hard_purge_soft_deleted：物理删除过期软删 chunk
"""

from __future__ import annotations

import logging
from typing import Any

from app.knowledge.infrastructure.doc_parser.retrieval.config import RetrievalConfig
from app.knowledge.infrastructure.doc_parser.retrieval.doc_lifecycle import (
    DEFAULT_QUERY_LIMIT,
    build_soft_delete_record,
    doc_id_filter,
    escape_milvus_string,
    hard_purge_filter,
    merge_active_filter,
    next_version,
    now_ts,
    tenant_boundary_filter,
    tenant_visibility_filter,
    validate_doc_id,
)
from app.shared.core.async_bridge import run_blocking
from app.shared.retrieval import MilvusHybridSearchCore
from pymilvus import DataType, Function, FunctionType, MilvusClient

logger = logging.getLogger(__name__)


def _max_version_of(rows: list[dict[str, Any]]) -> int:
    """从 query 结果里取最大 version，脏值按 0 处理。"""
    max_v = 0
    for row in rows:
        try:
            max_v = max(max_v, int(row.get("version") or 0))
        except (TypeError, ValueError):
            continue
    return max_v


class MilvusStore:
    """Milvus 向量存储。

    管理 RAG 文档的向量索引、软删更新与相似度检索。
    """

    def __init__(self, config: RetrievalConfig, embedding_model=None):
        """初始化 Milvus 连接和 Collection。

        Args:
            config: 检索配置。
            embedding_model: Embedding 模型，需要有 embed_query(text) -> List[float] 方法。
        """
        self.config = config
        self.embedding_model = self._resolve_embedding_model(embedding_model)
        self.client = MilvusClient(uri=f"http://{config.milvus_host}:{config.milvus_port}")
        self._create_collection_if_not_exists()
        self.retrieval_core = MilvusHybridSearchCore(
            milvus_client=self.client,
            embedding_model=self.embedding_model,
            collection_name=self.config.milvus_collection_name,
            dense_field="embedding",
            sparse_field="sparse_vector",
            dense_metric_type=self.config.milvus_metric_type,
            dense_search_params={"nprobe": self.config.milvus_nlist},
            hybrid_rrf_k=self.config.rrf_k,
            bm25_drop_ratio=self.config.bm25_drop_ratio,
        )

    def _resolve_embedding_model(self, embedding_model):
        """解析文档检索使用的 embedding 模型。

        未显式注入时走全局共享工厂——**必须**和长期记忆用同一个模型，
        否则两边向量落在不同语义空间。这里曾经自己读 os.getenv 并自带一套
        默认值，形成第二个配置真相来源，见 `app.shared.core.embeddings`。
        """
        if embedding_model is not None:
            return embedding_model

        from app.shared.core.embeddings import get_embedding_model

        return get_embedding_model()

    # ------------------------------------------------------------------ #
    # Collection 管理
    # ------------------------------------------------------------------ #

    def _create_collection_if_not_exists(self):
        """创建 Collection（如果不存在）。

        Schema:
        - chunk_id: VARCHAR(64) PRIMARY KEY
        - doc_id: VARCHAR(64)
        - source_file: VARCHAR(512)
        - chunk_type: VARCHAR(32)
        - section_path: VARCHAR(512)
        - raw_text: VARCHAR(8192)
        - embedding_text: VARCHAR(8192)
        - version: INT64  文档版本（从 1 递增）
        - is_deleted: BOOL  软删除标记
        - updated_at: INT64  Unix 秒，插入/软删时更新
        - content_hash: VARCHAR(64)  可选内容哈希（幂等）
        - embedding: FLOAT_VECTOR
        - sparse_vector: BM25 Function 输出
        """
        name = self.config.milvus_collection_name
        if self.client.has_collection(name):
            logger.info("Collection %s 已存在", name)
            return

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=True)

        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("source_file", DataType.VARCHAR, max_length=512)
        schema.add_field("chunk_type", DataType.VARCHAR, max_length=32)
        schema.add_field("section_path", DataType.VARCHAR, max_length=512)
        schema.add_field("raw_text", DataType.VARCHAR, max_length=8192)
        schema.add_field("embedding_text", DataType.VARCHAR, max_length=8192)
        schema.add_field("version", DataType.INT64)
        schema.add_field("is_deleted", DataType.BOOL)
        schema.add_field("updated_at", DataType.INT64)
        schema.add_field("content_hash", DataType.VARCHAR, max_length=64)
        # 可见域："global" 为共享知识库；私有文档为上传者 user_id 字符串
        schema.add_field("owner_id", DataType.VARCHAR, max_length=64)
        # 租户边界："" 表示平台公共（visibility=global）；否则为本租户 chunk。
        # SaaS 检索过滤的硬性隔离维度，任何查询都必须叠加
        schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
        # 三级可见性：global | tenant | private
        schema.add_field("visibility", DataType.VARCHAR, max_length=32)
        bm25_fn = Function(
            name="bm25",
            function_type=FunctionType.BM25,
            input_field_names=["raw_text"],
            output_field_names=["sparse_vector"],
        )
        schema.add_function(bm25_fn)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field(
            "embedding",
            DataType.FLOAT_VECTOR,
            dim=self.config.milvus_embedding_dim,
        )

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type=self.config.milvus_index_type,
            metric_type=self.config.milvus_metric_type,
            params={"nlist": self.config.milvus_nlist},
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )

        self.client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=index_params,
        )
        logger.info("Collection %s 创建成功（含 is_deleted/version）", name)

    # ------------------------------------------------------------------ #
    # 数据操作
    # ------------------------------------------------------------------ #

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量生成 chunk 向量。

        WHY 批量而不是逐条：索引一篇文档会产出几十到几百个 chunk。
        逐条 `embed_query` 就是几十到几百次模型前向 / HTTP 往返，
        而 `embed_documents` 只走一次批推理，是数量级的差距。
        """
        if self.embedding_model is None:
            raise RuntimeError("embedding_model 未设置，无法生成向量")
        vectors = await self.retrieval_core.embed_documents(texts)
        if vectors is None or len(vectors) != len(texts):
            raise RuntimeError(
                f"embedding 批量生成失败或数量不匹配 | expected={len(texts)} "
                f"got={0 if vectors is None else len(vectors)}"
            )
        return vectors

    async def _query(
        self,
        filter_expr: str,
        *,
        output_fields: list[str],
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        """统一封装 Milvus 标量 query（同步 SDK → 线程池）。"""
        rows = await run_blocking(
            self.client.query,
            collection_name=self.config.milvus_collection_name,
            filter=filter_expr,
            output_fields=output_fields,
            limit=limit,
        )
        return [row for row in (rows or []) if isinstance(row, dict)]

    async def get_max_version(self, doc_id: str, tenant_id: str = "") -> int:
        """查询某租户下文档历史最大 version（含已软删），不存在则 0。"""
        safe_doc = validate_doc_id(doc_id)
        expr = doc_id_filter(safe_doc, active_only=False)
        if tenant_id:
            expr = f'(tenant_id == "{escape_milvus_string(tenant_id)}") and ({expr})'
        try:
            rows = await self._query(expr, output_fields=["version"])
        except Exception as exc:
            logger.warning("get_max_version 失败 | doc_id=%s | %s", safe_doc, exc)
            return 0
        return _max_version_of(rows)

    async def soft_delete_by_doc_id(
        self,
        doc_id: str,
        tenant_id: str = "",
    ) -> dict[str, int]:
        """软删除某租户下文档的全部未删 chunk。

        tenant_id 为空时不做租户过滤（兼容存量未打标 chunk 的清理路径）。

        Returns:
            {"soft_deleted": n, "max_version": v}
        """
        safe_doc = validate_doc_id(doc_id)
        expr = doc_id_filter(safe_doc, active_only=True)
        if tenant_id:
            expr = f'(tenant_id == "{escape_milvus_string(tenant_id)}") and ({expr})'
        try:
            rows = await self._query(expr, output_fields=["chunk_id", "version"])
        except Exception as exc:
            logger.error(
                "soft_delete_by_doc_id query 失败 | doc_id=%s tenant=%s | %s",
                safe_doc,
                tenant_id,
                exc,
                exc_info=True,
            )
            return {
                "soft_deleted": 0,
                "max_version": await self.get_max_version(safe_doc, tenant_id),
            }

        if not rows:
            return {
                "soft_deleted": 0,
                "max_version": await self.get_max_version(safe_doc, tenant_id),
            }

        max_v = _max_version_of(rows)
        ts = now_ts()
        records = [
            build_soft_delete_record(str(row["chunk_id"]), updated_at=ts)
            for row in rows
            if row.get("chunk_id")
        ]
        if not records:
            return {"soft_deleted": 0, "max_version": max_v}

        try:
            await run_blocking(
                self.client.upsert,
                collection_name=self.config.milvus_collection_name,
                data=records,
            )
        except Exception as exc:
            logger.error(
                "soft_delete_by_doc_id upsert 失败 | doc_id=%s | %s",
                safe_doc,
                exc,
                exc_info=True,
            )
            return {"soft_deleted": 0, "max_version": max_v}

        logger.info(
            "RAG 软删除完成 | doc_id=%s soft_deleted=%s max_version=%s",
            safe_doc,
            len(records),
            max_v,
        )
        return {"soft_deleted": len(records), "max_version": max_v}

    async def insert_chunks(
        self,
        chunks: list[Any],
        *,
        version: int = 1,
        content_hash: str = "",
        owner_id: str = "global",
        tenant_id: str = "",
        visibility: str = "global",
        idempotency_key: str = "",
    ) -> int:
        """批量插入 DocumentChunk 到 Milvus。

        Args:
            chunks: DocumentChunk 列表。
            version: 文档版本号，默认 1。
            content_hash: 可选内容哈希。
            owner_id: 私有文档的所有者标识；公共文档为 global_owner。
            tenant_id: 租户边界；global 可见性文档为空串（平台公共）。
            visibility: global | tenant | private 三级可见性。

        Returns:
            成功插入的数量。
        """
        if not chunks:
            return 0

        version = max(1, int(version))
        hash_value = (content_hash or "")[:64]
        ts = now_ts()
        vectors = await self._embed_texts(
            [chunk.embedding_text or chunk.raw_text for chunk in chunks]
        )
        data: list[dict[str, Any]] = [
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "source_file": chunk.source_file,
                "chunk_type": chunk.chunk_type,
                "section_path": chunk.section_path,
                "raw_text": chunk.raw_text,
                "embedding_text": chunk.embedding_text,
                "version": version,
                "is_deleted": False,
                "updated_at": ts,
                "content_hash": hash_value,
                "owner_id": (owner_id or "global")[:64],
                "tenant_id": (tenant_id or "")[:64],
                "visibility": (visibility or "global")[:32],
                "embedding": vector,
            }
            # strict=True 冗余但明确：_embed_texts 已保证长度一致，
            # 万一底层模型返回数量不符，宁可炸出来也不要静默丢 chunk
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

        write = self.client.upsert if idempotency_key else self.client.insert
        result = await run_blocking(
            write,
            collection_name=self.config.milvus_collection_name,
            data=data,
        )
        count = result.get("insert_count", 0) if isinstance(result, dict) else 0
        logger.info("插入 %s 条记录到 Milvus | version=%s", count, version)
        return int(count or len(data))

    async def reindex_document(
        self,
        doc_id: str,
        chunks: list[Any],
        *,
        content_hash: str = "",
        owner_id: str = "global",
        tenant_id: str = "",
        visibility: str = "global",
        idempotency_key: str = "",
    ) -> dict[str, int]:
        """文档动态更新：软删旧版 → 插入新 version。

        Returns:
            soft_deleted / version / chunks
        """
        safe_doc = validate_doc_id(doc_id)
        if idempotency_key:
            existing = await self._get_active_event_index(
                safe_doc,
                idempotency_key,
                tenant_id,
            )
            if existing is not None:
                return {
                    "soft_deleted": 0,
                    "version": existing["version"],
                    "chunks": existing["chunks"],
                }
        delete_info = await self.soft_delete_by_doc_id(safe_doc, tenant_id)
        version = next_version(delete_info.get("max_version"))
        # 确保 chunks 上的 doc_id 一致（防止解析侧漂移）
        for chunk in chunks:
            if getattr(chunk, "doc_id", None) != safe_doc:
                try:
                    chunk.doc_id = safe_doc
                except Exception:
                    pass
        inserted = await self.insert_chunks(
            chunks,
            version=version,
            content_hash=content_hash,
            owner_id=owner_id,
            tenant_id=tenant_id,
            visibility=visibility,
            idempotency_key=idempotency_key,
        )
        return {
            "soft_deleted": int(delete_info.get("soft_deleted") or 0),
            "version": version,
            "chunks": inserted,
        }

    async def _get_active_event_index(
        self,
        doc_id: str,
        idempotency_key: str,
        tenant_id: str = "",
    ) -> dict[str, int] | None:
        """返回本次事件已写入的活跃版本，供 XACK 前重放快速收敛。"""
        marker = f"evt_{idempotency_key}_"
        expr = doc_id_filter(doc_id, active_only=True)
        if tenant_id:
            expr = f'(tenant_id == "{escape_milvus_string(tenant_id)}") and ({expr})'
        try:
            rows = await self._query(expr, output_fields=["chunk_id", "version"])
        except Exception as exc:
            logger.warning("读取事件索引状态失败 | doc_id=%s | %s", doc_id, exc)
            return None
        matched = [row for row in rows if str(row.get("chunk_id") or "").startswith(marker)]
        if not matched:
            return None
        return {"version": _max_version_of(matched), "chunks": len(matched)}

    async def hard_purge_soft_deleted(
        self,
        *,
        retention_seconds: int = 7 * 24 * 3600,
        batch_limit: int = DEFAULT_QUERY_LIMIT,
    ) -> int:
        """物理删除已软删且超过保留期的 chunk。

        Returns:
            删除条数；失败返回 0。
        """
        if retention_seconds < 0 or batch_limit <= 0:
            return 0
        cutoff = now_ts() - int(retention_seconds)
        try:
            rows = await self._query(
                hard_purge_filter(cutoff_ts=cutoff),
                output_fields=["chunk_id"],
                limit=batch_limit,
            )
            chunk_ids = [str(row["chunk_id"]) for row in rows if row.get("chunk_id")]
            if not chunk_ids:
                return 0

            ids_literal = ", ".join(f'"{cid}"' for cid in chunk_ids)
            await run_blocking(
                self.client.delete,
                collection_name=self.config.milvus_collection_name,
                filter=f"chunk_id in [{ids_literal}]",
            )
            logger.info(
                "RAG 硬清理完成 | deleted=%s cutoff=%s collection=%s",
                len(chunk_ids),
                cutoff,
                self.config.milvus_collection_name,
            )
            return len(chunk_ids)
        except Exception as exc:
            logger.error(
                "hard_purge_soft_deleted 异常 | collection=%s | %s",
                self.config.milvus_collection_name,
                exc,
                exc_info=True,
            )
            return 0

    # ------------------------------------------------------------------ #
    # 向量检索
    # ------------------------------------------------------------------ #

    @staticmethod
    def _visibility_filter() -> str | None:
        """按请求上下文构造检索过滤（租户隔离 + 可选可见性精排）。

        两层语义：
        1. 租户边界（**常开**，SaaS 隔离底线）：本租户 chunk + 平台公共
        2. 可见性精排（rag_visibility.enabled 开启时）：global/tenant/private

        身份来自 contextvars（认证依赖写入），检索层无需层层传参；
        无认证上下文（脚本/评测）只能看公共 + 默认租户数据。
        """
        from app.shared.core.config import settings as app_settings
        from app.shared.core.identity import (
            get_current_tenant_id,
            get_current_user_id,
        )

        tenant_id = get_current_tenant_id()
        scope = tenant_boundary_filter(tenant_id)

        visibility = app_settings.app_config.rag_visibility
        if not visibility.enabled:
            return scope

        user_id = get_current_user_id()
        visibility_scope = tenant_visibility_filter(
            tenant_id,
            str(user_id) if user_id is not None else None,
            global_owner=visibility.global_owner,
        )
        return f"({scope}) and ({visibility_scope})"

    def _merge_visibility(self, filter_expr: str | None) -> str | None:
        scope = self._visibility_filter()
        if scope is None:
            return filter_expr
        if not filter_expr or not str(filter_expr).strip():
            return scope
        return f"({scope}) and ({str(filter_expr).strip()})"

    def _search_output_fields(self) -> list[str]:
        return [
            "chunk_id",
            "doc_id",
            "source_file",
            "chunk_type",
            "section_path",
            "raw_text",
            "embedding_text",
            "version",
            "is_deleted",
            "content_hash",
        ]

    def _format_hits(self, hits: list[dict[str, Any]], score_key: str) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for hit in hits:
            entity = hit["entity"]
            formatted.append(
                {
                    "chunk_id": entity.get("chunk_id", ""),
                    "doc_id": entity.get("doc_id", ""),
                    "source_file": entity.get("source_file", ""),
                    "chunk_type": entity.get("chunk_type", ""),
                    "section_path": entity.get("section_path", ""),
                    "raw_text": entity.get("raw_text", ""),
                    "embedding_text": entity.get("embedding_text", ""),
                    "version": entity.get("version", 0),
                    "content_hash": entity.get("content_hash", ""),
                    score_key: hit["score"],
                }
            )
        return formatted

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        filter_expr: str | None = None,
        *,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        """向量相似度检索（默认排除软删）。"""
        top_k = top_k or self.config.vector_top_k
        scoped = self._merge_visibility(filter_expr)
        effective = scoped if include_deleted else merge_active_filter(scoped)
        hits = await self.retrieval_core.search_dense(
            query,
            limit=top_k,
            filter_expr=effective,
            output_fields=self._search_output_fields(),
        )
        return self._format_hits(hits, "vector_score")

    async def hybrid_search(
        self,
        query: str,
        top_k: int | None = None,
        filter_expr: str | None = None,
        *,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        """Native Milvus hybrid search for document retrieval."""
        top_k = top_k or self.config.rrf_final_top_k
        search_limit = max(self.config.vector_top_k, self.config.bm25_top_k, top_k)
        scoped = self._merge_visibility(filter_expr)
        effective = scoped if include_deleted else merge_active_filter(scoped)
        hits = await self.retrieval_core.search_hybrid(
            query,
            limit=top_k,
            filter_expr=effective,
            output_fields=self._search_output_fields(),
            search_limit=search_limit,
        )
        return self._format_hits(hits, "rrf_score")
