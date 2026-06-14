# KG 子图说明

`app/chat/infrastructure/kg_sub_graph/` 仍然是当前项目里知识图谱检索链路的有效组成部分，并没有被完全移除。它的定位不是直接给 FastAPI 或主图节点调用，而是作为 `app.chat.infrastructure.retrievers.retriever_runtime` 背后的 KG 实现细节存在。

## 结构分工

- `kg_neo4j_conn.py`
  - 负责 Neo4j 连接与图数据库访问基础能力。
- `agentic_rag_agents/`
  - 承载 Text2Cypher、预定义 Cypher 模板、示例检索和单 Agent 工作流。
  - 是 KG 检索“真正干活”的实现目录。
  - 其中 Text2Cypher 工作流组装已经收口到 `workflows/single_agent/text2cypher.py`；只被该工作流单独消费的生成 / 校验 / 修正节点工厂已并回主流程，避免在 `components/` 下保留三份专用薄壳模块。
  - Text2Cypher 校验规则仍按职责拆分：`validators.py` 负责入口编排，`schema_validation_rules.py` 负责纯 schema 校验细节。
  - 其中预定义 Cypher 匹配链路当前已收口到 `utils.py`：主流程、embedding payload、查询文本拼装和参数 / JSON 提取 helper 现在集中在同一模块，避免为了几段纯函数再拆出单消费者薄壳文件。
  - KG 相关提示词现在跟随各自组件就近维护，不再单独保留一份未接入运行时的集中提示词目录。
  - 不再承载独立的文档检索节点；文档 RAG 已收敛到顶层检索器和 `rag_doc_parser` 链路。
  - 当前保留的是主链实际在用的 few-shot 示例检索实现；未接入的 Neo4j 向量示例检索旁支和旧配置加载工具已移除。

## 当前边界

- `kg_sub_graph/` 只负责图谱检索和 Text2Cypher 相关能力，不负责 HTTP 协议转换。
- 主图节点不应该直接依赖这里的细节，应该优先通过 `app.chat.infrastructure.retrievers.retriever_runtime` 暴露出来的统一接口访问。
- 如果只是调整顶层策略分流或回答拼装，优先改图节点或 ReAct 层，不要直接把逻辑塞回 KG 子图。

## 维护建议

- 如果后续继续弱化 KG 方案，应优先从“对外入口是否还被 `Retriever` 调用”来判断，而不是只看目录名是否还在。
- 如果只是在 KG 子图内部做清理，优先删除未被导出的 helper 和重复格式化逻辑，避免主流程层看到更多实现细节。
- 如果某个子目录既没有外部引用、也不再参与 `Text2Cypher` 图组装，应直接删除，而不是继续保留空壳包。
