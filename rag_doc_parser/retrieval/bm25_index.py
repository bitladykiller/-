"""
BM25 关键词检索引擎。

在内存中维护文档索引，支持 BM25 分数计算和检索。
中文按 UTF-8 字节 + 英文单词分词。
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from rag_doc_parser.retrieval.config import RetrievalConfig


def _tokenize(text: str) -> List[str]:
    """中文逐字 + 英文按词 分词。

    中文按每个汉字切分，英文按单词切分。
    小写处理，去除纯标点。
    """
    tokens = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z]+", text.lower())
    return tokens


class BM25Index:
    """BM25 关键词索引。

    在内存中维护文档列表，支持增量添加和批量检索。
    不持久化，进程重启后需重建。
    """

    def __init__(self, config: RetrievalConfig):
        self.config = config
        self.k1 = config.bm25_k1
        self.b = config.bm25_b
        self.documents: List[Dict[str, Any]] = []  # 原始文档
        self.doc_tokens: List[List[str]] = []  # 每篇文档的分词结果
        self.doc_len: List[int] = []  # 每篇文档的分词数
        self.avg_doc_len: float = 0.0  # 平均文档长度
        self.df: Dict[str, int] = {}  # 文档频率（词在多少篇文档中出现）
        self.N: int = 0  # 总文档数

    # ------------------------------------------------------------------ #
    # 索引构建
    # ------------------------------------------------------------------ #

    def add_document(self, chunk_id: str, text: str, metadata: Dict[str, Any] = None):
        """添加单篇文档到索引。

        Args:
            chunk_id: 文档 chunk ID。
            text: 文档文本（用于 BM25 匹配）。
            metadata: 附加元信息。
        """
        tokens = _tokenize(text)
        if not tokens:
            return

        self.documents.append({"chunk_id": chunk_id, "text": text, "metadata": metadata or {}})
        self.doc_tokens.append(tokens)
        self.doc_len.append(len(tokens))

        # 更新文档频率
        seen = set()
        for token in tokens:
            if token not in seen:
                self.df[token] = self.df.get(token, 0) + 1
                seen.add(token)

        self.N = len(self.documents)
        self.avg_doc_len = sum(self.doc_len) / self.N if self.N > 0 else 0.0

    def add_documents(self, chunks: List[Any]):
        """批量添加文档。

        Args:
            chunks: DocumentChunk 列表。
        """
        for chunk in chunks:
            self.add_document(
                chunk_id=chunk.chunk_id,
                text=getattr(chunk, self.config.sparse_field, chunk.raw_text),
                metadata={
                    "doc_id": chunk.doc_id,
                    "source_file": chunk.source_file,
                    "chunk_type": chunk.chunk_type,
                    "section_path": chunk.section_path,
                },
            )
        logger = __import__("logging").getLogger(__name__)
        logger.info(f"BM25 索引构建完成，共 {self.N} 篇文档")

    # ------------------------------------------------------------------ #
    # BM25 检索
    # ------------------------------------------------------------------ #

    def _idf(self, term: str) -> float:
        """计算逆文档频率（IDF）。

        IDF = log((N - df + 0.5) / (df + 0.5) + 1)
        使用 BM25 标准 IDF 公式。
        """
        df = self.df.get(term, 0)
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1)

    def _bm25_score(self, query_tokens: List[str], doc_idx: int) -> float:
        """计算单篇文档的 BM25 分数。

        Args:
            query_tokens: 查询的分词列表。
            doc_idx: 文档在 self.documents 中的索引。

        Returns:
            BM25 分数。
        """
        score = 0.0
        doc_tokens = self.doc_tokens[doc_idx]
        doc_len = self.doc_len[doc_idx]

        # 词频统计
        tf = {}
        for token in doc_tokens:
            tf[token] = tf.get(token, 0) + 1

        for token in query_tokens:
            if token not in tf:
                continue
            idf = self._idf(token)
            f = tf[token]
            numerator = f * (self.k1 + 1)
            denominator = f + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_len)
            score += idf * numerator / denominator

        return score

    def search(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """BM25 检索。

        Args:
            query: 查询文本。
            top_k: 返回条数（默认使用 config.bm25_top_k）。

        Returns:
            检索结果列表，每项包含 chunk 信息 + bm25_score。
        """
        top_k = top_k or self.config.bm25_top_k
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # 计算所有文档的 BM25 分数
        scores = [
            (i, self._bm25_score(query_tokens, i))
            for i in range(self.N)
        ]
        scores.sort(key=lambda x: x[1], reverse=True)

        # 取 top_k
        results = []
        for doc_idx, score in scores[:top_k]:
            if score <= 0:
                break
            doc = self.documents[doc_idx]
            results.append({
                **doc["metadata"],
                "chunk_id": doc["chunk_id"],
                "raw_text": doc["text"],
                "bm25_score": score,
            })

        return results

    def clear(self):
        """清空索引。"""
        self.documents.clear()
        self.doc_tokens.clear()
        self.doc_len.clear()
        self.df.clear()
        self.N = 0
        self.avg_doc_len = 0.0
