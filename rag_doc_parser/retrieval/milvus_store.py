"""
Milvus 向量存储 — Collection 管理与混合检索。

支持创建 Collection、批量插入 DocumentChunk、
以及基于 Milvus native hybrid search 的检索。
"""

import logging
import os
from typing import Any

from pymilvus import DataType, Function, FunctionType, MilvusClient
from shared_retrieval.milvus_hybrid_core import MilvusHybridSearchCore

from rag_doc_parser.retrieval.config import RetrievalConfig

logger = logging.getLogger(__name__)


class MilvusStore:
    """Milvus 向量存储。

    管理 RAG 文档的向量索引和混合检索。
    """

    def __init__(self, config: RetrievalConfig, embedding_model=None):
        """初始化 Milvus 连接和 Collection。

        Args:
            config: 检索配置。
            embedding_model: Embedding 模型，需要有 embed_query(text) -> List[float] 方法。
        """
        self.config = config
        self.embedding_model = self._resolve_embedding_model(embedding_model)
        self.client = MilvusClient(
            uri=f"http://{config.milvus_host}:{config.milvus_port}"
        )
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
        )

    def _resolve_embedding_model(self, embedding_model):
        """Resolve the embedding model for document retrieval.

        RAG retrieval is used from multiple entry points and many of them do not
        explicitly inject an embedding model. Resolve a default model here so the
        retrieval stack remains self-contained.
        """
        if embedding_model is not None:
            return embedding_model

        model_name = os.getenv("OLLAMA_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL") or "bge-m3"
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

        try:
            from langchain_ollama import OllamaEmbeddings

            logger.info("RAG retrieval using default Ollama embeddings: %s", model_name)
            return OllamaEmbeddings(model=model_name, base_url=base_url)
        except Exception:
            try:
                from langchain_community.embeddings import HuggingFaceEmbeddings

                logger.info("RAG retrieval using default HuggingFace embeddings: %s", model_name)
                return HuggingFaceEmbeddings(model_name=model_name)
            except Exception as exc:
                logger.error("Failed to resolve default embedding model for RAG retrieval: %s", exc, exc_info=True)
                raise RuntimeError("embedding_model 未设置，且无法创建默认 embedding 模型") from exc

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
        - embedding: FLOAT_VECTOR(1024) → bge-m3
        """
        name = self.config.milvus_collection_name
        if self.client.has_collection(name):
            logger.info(f"Collection {name} 已存在")
            return

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=True)

        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("source_file", DataType.VARCHAR, max_length=512)
        schema.add_field("chunk_type", DataType.VARCHAR, max_length=32)
        schema.add_field("section_path", DataType.VARCHAR, max_length=512)
        schema.add_field("raw_text", DataType.VARCHAR, max_length=8192)
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
        logger.info(f"Collection {name} 创建成功")

    # ------------------------------------------------------------------ #
    # 数据操作
    # ------------------------------------------------------------------ #

    async def _get_embedding(self, text: str) -> list[float]:
        """获取文本的 embedding 向量。

        Args:
            text: 输入文本。

        Returns:
            1024 维浮点数列表。
        """
        if self.embedding_model is None:
            raise RuntimeError("embedding_model 未设置，无法生成向量")
        return self.embedding_model.embed_query(text)

    async def insert_chunks(self, chunks: list[Any]) -> int:
        """批量插入 DocumentChunk 到 Milvus。

        Args:
            chunks: DocumentChunk 列表。

        Returns:
            成功插入的数量。
        """
        if not chunks:
            return 0

        data = []
        for chunk in chunks:
            text = chunk.embedding_text or chunk.raw_text
            vector = await self._get_embedding(text)

            data.append({
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "source_file": chunk.source_file,
                "chunk_type": chunk.chunk_type,
                "section_path": chunk.section_path,
                "raw_text": chunk.raw_text,
                "embedding": vector,
            })

        result = self.client.insert(
            collection_name=self.config.milvus_collection_name,
            data=data,
        )
        count = result.get("insert_count", 0)
        logger.info(f"插入 {count} 条记录到 Milvus")
        return count

    async def hybrid_search(
        self,
        query: str,
        top_k: int | None = None,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        """Native Milvus hybrid search for document retrieval."""
        top_k = top_k or self.config.rrf_final_top_k
        search_limit = max(self.config.vector_top_k, self.config.bm25_top_k, top_k)
        hits = await self.retrieval_core.search_hybrid(
            query,
            limit=top_k,
            filter_expr=filter_expr,
            output_fields=[
                "source_file", "chunk_type", "section_path", "raw_text",
            ],
            search_limit=search_limit,
        )

        formatted = []
        for hit in hits:
            entity = hit["entity"]
            formatted.append({
                "source_file": entity.get("source_file", ""),
                "chunk_type": entity.get("chunk_type", ""),
                "section_path": entity.get("section_path", ""),
                "raw_text": entity.get("raw_text", ""),
                "rrf_score": hit["score"],
            })
        return formatted
