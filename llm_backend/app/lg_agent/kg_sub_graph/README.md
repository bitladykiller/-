# KG 子图说明

`llm_backend/app/lg_agent/kg_sub_graph/` 仍然是当前项目里知识图谱检索链路的有效组成部分，并没有被完全移除。它的定位不是直接给 FastAPI 或主图节点调用，而是作为 `lg_retrievers.py` 背后的 KG 实现细节存在。

## 结构分工

- `kg_neo4j_conn.py`
  - 负责 Neo4j 连接与图数据库访问基础能力。
- `prompts/`
  - 存放 KG 子图内部使用的提示词常量。
  - 只保留仍在使用的 prompt 导出，不再夹带已经废弃的 schema helper。
- `agentic_rag_agents/`
  - 承载 Text2Cypher、预定义 Cypher 模板、示例检索和单 Agent 工作流。
  - 是 KG 检索“真正干活”的实现目录。
  - 其中 Text2Cypher 校验链路当前已拆成 `node.py`（流程编排）、`validators.py`（对外入口）和 `schema_validation_rules.py`（纯 schema 规则），避免一个文件同时混着入口和规则细节。
  - 其中预定义 Cypher 匹配链路当前已拆成 `utils.py`（向量匹配主流程）和 `predefined_cypher_support.py`（参数提取 / JSON 解析 / payload helper），避免一个类同时承担网络调用和大段文本处理细节。
  - 不再承载独立的文档检索节点；文档 RAG 已收敛到顶层 `lg_retrievers.py` / `rag_doc_parser` 链路。
  - 当前保留的是主链实际在用的 few-shot 示例检索实现；未接入的 Neo4j 向量示例检索旁支和旧配置加载工具已移除。

## 当前边界

- `kg_sub_graph/` 只负责图谱检索和 Text2Cypher 相关能力，不负责 HTTP 协议转换。
- 主图节点不应该直接依赖这里的细节，应该优先通过 `lg_retrievers.py` 暴露出来的统一接口访问。
- 如果只是调整顶层策略分流或回答拼装，优先改 `lg_nodes.py` / `lg_react.py`，不要直接把逻辑塞回 KG 子图。

## 维护建议

- 如果后续继续弱化 KG 方案，应优先从“对外入口是否还被 `Retriever` 调用”来判断，而不是只看目录名是否还在。
- 如果只是在 KG 子图内部做清理，优先删除未被导出的 helper 和重复格式化逻辑，避免主流程层看到更多实现细节。
- 如果某个子目录既没有外部引用、也不再参与 `Text2Cypher` 图组装，应直接删除，而不是继续保留空壳包。
