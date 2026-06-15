"""Shared retrieval infrastructure used by both RAG and long-term memory.

This package provides domain-agnostic Milvus hybrid search capabilities
(dense + BM25 sparse + RRF fusion) shared by:
- rag_doc_parser (document chunk retrieval)
- app.knowledge.infrastructure.ltm (long-term memory retrieval)

Install: pip install -e ./shared_retrieval
"""
