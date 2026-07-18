# KG 子模块说明

`app/chat/infrastructure/kg/` 是知识图谱检索链路的实现细节，供
`app.chat.infrastructure.retrievers.retriever_runtime` 调用，不直接暴露给 FastAPI 路由。

## 结构

- `neo4j_conn.py` — Neo4j 连接
- `text2cypher_workflow.py` — Text2Cypher 单 Agent 图组装
- `text2cypher_state.py` — Text2Cypher 状态类型
- `northwind_retriever.py` — few-shot Cypher 示例检索
- `predefined_cypher/` — 预定义模板与匹配工具
- `validation/` — Cypher 校验模型与规则

## 边界

- 只负责图谱检索与 Text2Cypher，不负责 HTTP 协议转换
- 主图节点应通过 `retriever_runtime` 访问，不直接依赖本目录细节
