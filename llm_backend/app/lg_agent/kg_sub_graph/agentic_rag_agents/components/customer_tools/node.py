"""
RAG 文档检索节点（替代原 Microsoft GraphRAG customer_tools）。

使用 rag_doc_parser + Milvus 向量检索 + BM25 + RRF + Reranker。
"""

from typing import Any, Dict



def create_rag_search_node():
    """创建 RAG 文档检索节点。

    替代原 GraphRAG 查询节点。用户上传的文档通过 rag_doc_parser 解析后
    存入 Milvus + BM25 索引，此节点负责从索引中检索相关片段。

    如果 rag_doc_parser 未安装或索引未构建，返回友好提示。
    """

    async def rag_search(state: Dict[str, Any]) -> Dict[str, Any]:
        task = state.get("task", "")
        query = task if isinstance(task, str) else str(task)
        errors = []

        try:
            from rag_doc_parser.retrieval.hybrid_search import HybridSearcher
            from rag_doc_parser.retrieval.config import RetrievalConfig

            config = RetrievalConfig()
            searcher = HybridSearcher(config)
            results = await searcher.search(query)

            if results:
                records = {
                    "result": "\n\n".join(
                        f"[{r.get('chunk_type', 'text')}] {r.get('section_path', '')}\n{r.get('raw_text', '')}"
                        for r in results[:5]
                    )
                }
            else:
                records = {"result": "未在文档知识库中找到相关信息。"}
        except ImportError:
            records = {"result": "文档检索模块未安装。请先上传文档建立知识库。"}
            errors.append("rag_doc_parser 模块未安装")
        except Exception as e:
            records = {"result": "文档检索暂时不可用。"}
            errors.append(str(e))

        return {
            "cyphers": [{
                "task": task,
                "records": records,
                "errors": errors,
                "steps": ["execute_rag_search"],
            }],
            "steps": ["execute_rag_search"],
        }

    return rag_search
