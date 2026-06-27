# KG 子模块说明

`app/chat/infrastructure/kg/` 是知识图谱检索链路的实现细节，供
`app.chat.infrastructure.retrievers.retriever_runtime` 调用，不直接暴露给 FastAPI 路由。

## 结构

- `neo4j_conn.py` — Neo4j 连接
- `text2cypher_workflow.py` — Text2Cypher 单 Agent 图组装（含预定义快路径）
- `text2cypher_state.py` — Text2Cypher 状态类型
- `northwind_retriever.py` — few-shot Cypher 示例检索（给 LLM 生成路径）
- `predefined_cypher/` — 预定义模板与语义匹配
  - `cypher_dict.py` — 模板字典
  - `descriptions.py` — 模板描述（拼入匹配文本）
  - `utils.py` — `_VectorQueryMatcher`、参数提取、`cosine_similarity_score`
- `validation/` — Cypher 校验模型与规则

## 预定义模板匹配（快路径）

```text
模板文本 = query_name + description
  → Ollama /api/embed（失败则零向量降级）
  → cosine_similarity_score(question, template)   # 自研 NumPy，不用 sklearn
  → match_query 过滤 similarity >= 0.5
  → 工作流 predefined_match 再要求 similarity > 0.6 才走模板
  → extract_parameters 填参 → graph.query
```

**为何不用 sklearn.cosine_similarity：**

1. `cosine_similarity([ndarray], [ndarray])` 静态类型不清晰，易触发 Pylance 报错  
2. 零向量时 sklearn 可能得到 nan；`cosine_similarity_score` 对零向量固定返回 `0.0`  

## 边界

- 只负责图谱检索与 Text2Cypher，不负责 HTTP 协议转换
- 主图节点应通过 `retriever_runtime` 访问，不直接依赖本目录细节
