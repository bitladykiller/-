"""Shared retrieval infrastructure used by both RAG and long-term memory.

This package provides domain-agnostic Milvus hybrid search capabilities
(dense + BM25 sparse + RRF fusion) shared by:
- rag_doc_parser (document chunk retrieval)
- llm_backend (long-term memory retrieval)

Install: pip install -e ./shared_retrieval
"""

__version__ = "0.1.0"

from shared_retrieval.milvus_hybrid_core import MilvusHybridSearchCore

__all__ = ["MilvusHybridSearchCore"]
