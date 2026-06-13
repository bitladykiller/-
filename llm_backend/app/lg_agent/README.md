# LangGraph Agent 模块说明

`llm_backend/app/lg_agent/` 负责客服 Agent 的主流程编排。这个目录的目标不是把所有逻辑塞进一个大文件，而是把“图组装”“节点逻辑”“检索适配”“记忆上下文”“模型工厂”拆开，方便阅读和替换。

当前这个目录已经开始从“平铺文件 + support/runtime/helper 文件”迁移到“按能力分包”的结构：

- `graph/`：主图组装、节点、状态、边路由和消息入口
- `retrieval/`：检索抽象、注册表、KG/RAG 适配和摘要入口
- `react/`：ReAct 子图、运行时和 helper 入口
- `memory_bridge/`：Agent 和记忆系统之间的桥接入口
- `modeling/`：模型、Prompt 常量、默认值和加载入口

为了降低迁移风险，旧的 `lg_*` 平铺文件路径仍然保留为兼容入口，所以当前会同时看到：

1. 新的能力子包入口
2. 旧的平铺实现文件

后续新增代码应优先依赖能力子包路径，旧平铺路径只作为兼容层逐步收口。

## 结构分工

- `graph/`
  - `graph/builder.py` 暴露主图编译入口。
  - `graph/nodes.py` 暴露主图节点入口。
  - `graph/edges.py` 暴露路由与边选择相关 helper。
  - `graph/state.py` 暴露 `InputState`、`AgentState` 等状态结构。
  - `graph/messages.py` 暴露消息兼容与通用回复 helper。
- `lg_builder.py`
  - 只负责注册节点和连边。
  - 不写具体业务逻辑，避免图结构和节点实现混在一起。
- `lg_nodes.py`
  - 放主图节点：顶层路由、Guardrails、RetrievalPlan、简单执行器、响应后记忆写入。
  - 只依赖 `Retriever` 抽象，不直接依赖 Neo4j 或 RAG 的底层实现。
  - 当前已把简单执行器共享的“检索器调用 / 结果合并 / 摘要响应”样板收成 helper，执行节点只保留路径编排。
  - 当前也把边路由映射、问题包装和 `after_response` 消息提取下沉到 `lg_node_support.py`，主文件更像主图节点清单。
- `lg_node_support.py`
  - 负责 `lg_nodes.py` 共享的轻量 helper。
  - 当前承接边路由映射、system prompt 记忆追加、问题 XML 包装和记忆写回参数拼装。
  - 这样 `lg_nodes.py` 不再同时背负节点声明和一堆小型纯规则函数。
- `lg_react.py`
  - 放 ReAct 兜底执行链路。
  - 负责把检索器暴露成 `neo4j_query` / `rag_search` 两个工具，并做答案充分性检查。
  - 当前主文件更聚焦在“子图调用 + 充分性重试编排”，tool 输出格式化、transcript 拼装和 retry seed 构造已下沉到 `lg_react_support.py`。
- `lg_react_support.py`
  - 负责 `lg_react.py` 共享的纯 helper。
  - 当前承接工具输出序列化、主图回复格式化、transcript 截断、retry prompt 拼装和裁判输入消息构造。
  - 这样 `lg_react.py` 不再同时背负 ReAct 主流程和一串文本/消息格式化细节。
- `lg_react_runtime.py`
  - 负责 ReAct 子图的懒初始化和单例缓存。
  - 这样 `lg_react.py` 可以继续聚焦工具定义和重试编排，不再混着运行时缓存状态。
- `react/`
  - `react/graph.py` 暴露 ReAct 子图入口。
  - `react/runtime.py` 暴露 ReAct 运行时入口。
  - `react/helpers.py` 暴露 ReAct 共享 helper 入口。
- `lg_retrievers.py`
  - 定义 `Retriever` 接口、注册表和具体适配器。
  - 当前包含 `KnowledgeGraphRetriever` 和 `MilvusDocRetriever` 两个实现。
  - 当前主文件更聚焦在“接口 + 注册表 + 后端适配器”，结果归一化和文档片段字段提取已下沉到 `lg_retriever_support.py`。
- `lg_retriever_support.py`
  - 负责 `lg_retrievers.py` 共享的纯 helper。
  - 当前承接 records 收口、Text2Cypher 结果标准化、Milvus 文档片段字段提取和降级记录构造。
  - 这样 `lg_retrievers.py` 不再同时背负检索器适配和一串纯结果整理逻辑。
- `lg_retriever_runtime.py`
  - 负责 Retriever 注册表的懒初始化、KG/RAG 检索器注册，以及 Text2Cypher 子图 / Cypher 示例检索器缓存。
  - 这样 `lg_retrievers.py` 可以继续聚焦在抽象接口和后端适配，而不是同时背负模块级运行时状态。
- `lg_summarize.py`
  - 负责把检索到的 `records` 交给摘要节点生成最终回答片段。
  - 避免把“摘要生成”混进检索器注册与适配逻辑中。
- `retrieval/`
  - `retrieval/base.py` 暴露检索抽象入口。
  - `retrieval/registry.py` 暴露注册表与懒初始化入口。
  - `retrieval/kg.py` 和 `retrieval/rag.py` 暴露具体后端入口。
  - `retrieval/summarize.py` 暴露检索摘要入口。
  - `retrieval/runtime.py` 暴露运行时 helper 入口。
- `lg_execution_utils.py`
  - 负责简单执行器共享的检索样板：空结果占位、records 合并、查询改写、结构化输出调用和摘要回复拼装。
  - 让 `lg_nodes.py` 更聚焦“选哪条路径、按什么顺序执行”，而不是堆放检索执行细节。
- `lg_context.py`
  - 统一承接请求级记忆入口：加载当前请求的记忆状态、缓存到 `AgentState`、在需要时富化问题。
  - 不再负责运行时依赖初始化，也不再承担具体的上下文文本拼装。
- `lg_message_utils.py`
  - 负责消息兼容层：dict / LangChain Message 统一读取、user 消息安全包装、标准回复样板和“最后一条有效消息”提取。
  - 让 `lg_nodes.py` 继续聚焦主图流程，而不是同时背负消息清洗和回复拼装杂务。
- `lg_memory_runtime.py`
  - 负责 MemoryMiddleware 单例、Redis / Milvus / 抽取器的依赖创建，以及 startup / shutdown 生命周期。
  - 当前已改成延迟导入重依赖，保证 `lg_context.py` 这类请求入口模块可以独立测试。
- `lg_memory_prompt.py`
  - 负责把短期记忆、长期记忆、用户画像组装成可注入的上下文。
  - 当前统一承接记忆优先级说明、分段格式化和富化问题构造，让 `lg_context.py` 继续保持轻量的请求入口角色。
- `memory_bridge/`
  - `memory_bridge/context.py` 暴露请求级记忆上下文入口。
  - `memory_bridge/prompt.py` 暴露记忆上下文 Prompt 入口。
  - `memory_bridge/runtime.py` 暴露记忆依赖初始化和生命周期入口。
- `lg_models.py`
  - 管理 LLM 懒加载和结构化输出模型。
  - 让节点文件不再混入模型工厂和 Pydantic 输出定义。
- `lg_model_support.py`
  - 负责 `lg_models.py` 共享的运行时 helper。
  - 当前承接 provider 工厂选择、模型缓存 helper、温度映射和懒代理实现。
  - 这样 `lg_models.py` 可以更聚焦 provider 级模型创建入口和结构化输出模型定义。
- `lg_prompts.py`
  - 管理 Prompt 模板入口和 YAML 加载逻辑。
  - 当前主文件更聚焦公开常量入口和默认/覆盖 Prompt 的装配。
- `lg_prompt_support.py`
  - 负责 `lg_prompts.py` 共享的 Prompt 加载 helper。
  - 当前承接 YAML 路径解析、override 规范化、YAML 读取和默认值合并。
  - 这样 `lg_prompts.py` 可以更聚焦 Prompt 入口本身。
- `lg_prompt_defaults.py`
  - 负责 YAML 不可用时的默认 Prompt 文本常量。
  - 这样 `lg_prompts.py` 不再同时背负大段模板数据和加载入口逻辑。
- `modeling/`
  - `modeling/models.py` 暴露模型与结构化输出入口。
  - `modeling/prompts.py` 暴露 Prompt 常量入口。
  - `modeling/prompt_defaults.py` 暴露默认 Prompt 文本。
  - `modeling/prompt_loader.py` 暴露 YAML 加载与 mapping helper。
- `lg_states.py`
  - 定义输入状态、运行态和各类路由输出结构。
- `utils.py`
  - 存放跨模块共享的小型辅助函数，例如 UUID 生成和通用降级响应。
- `kg_sub_graph/`
  - 是知识图谱检索的底层实现细节。
  - 上层节点不直接感知 Text2Cypher 编排，只通过 `Retriever` 接口访问。
  - 其中 `retrievers/cypher_examples/northwind_retriever.py` 已按“示例常量 + 打分辅助方法 + 格式化输出”拆开，避免单个方法同时承载大段数据和流程逻辑。
  - 具体边界可参考 `kg_sub_graph/README.md`。

## 执行流程

1. `lg_builder.py` 组装主图。
2. `analyze_and_route_query()` 先区分通用对话和检索型问题。
3. 通用对话直接走 `respond_to_general_query()`，检索型问题先过 `guardrails_node()`。
4. `retrieval_plan_route()` 选择 5 条路径之一：
   - `GRAPH_ONLY`
   - `RAG_ONLY`
   - `PARALLEL`
   - `GRAPH_THEN_RAG`
   - `AGENT_REACT`
5. 简单路径在 `lg_nodes.py` 中执行，复杂兜底路径进入 `lg_react.py`。
6. 所有路径最终回到 `after_response()`，把本轮问答写回记忆系统。

## 当前边界设计

- 新代码优先依赖能力子包，而不是继续扩散 `lg_*` 平铺路径。
- 节点层负责流程编排，不负责创建底层连接。
- 检索器层负责把不同后端统一成 `records` 标准输出。
- 记忆层负责上下文注入，不和检索执行逻辑耦合。
- KG 子图仍然存在，但已经被收敛到 `lg_retrievers.py` 后面，不再污染主图节点。

## 后续维护建议

- 如果新增主图入口，优先放进 `graph/`。
- 如果新增检索入口，优先放进 `retrieval/`。
- 如果新增 ReAct 运行时入口，优先放进 `react/`。
- 如果新增记忆桥接入口，优先放进 `memory_bridge/`。
- 如果新增模型或 Prompt 入口，优先放进 `modeling/`。
- 如果新增检索后端，优先在 `lg_retrievers.py` 新增实现，不要直接改节点层分支。
- 如果新增执行策略，优先改 `lg_states.py` 的计划类型和 `lg_builder.py` 的路由连边。
- 如果只是改简单执行器共享的检索样板，优先改 `lg_execution_utils.py`，不要把 records 合并、查询改写、摘要回复拼装散落回多个执行节点。
- 如果只是改消息包装、占位回复或最后消息提取，优先改 `lg_message_utils.py`，不要把这类兼容代码散落回节点函数里。
- 如果只是改记忆注入方式，优先改 `lg_context.py` / `lg_memory_prompt.py`，不要在多个节点里重复拼装 `before_agent()` 参数。
- 如果只是改记忆依赖初始化、预热或关闭流程，优先改 `lg_memory_runtime.py`，不要把连接管理塞回节点层。
- 如果只是改边路由映射、问题包装或 `after_response` 参数拼装，优先改 `lg_node_support.py`，不要把这类纯 helper 再塞回节点函数里。
- 如果只是改 ReAct transcript、retry seed、tool 输出格式或裁判输入拼装，优先改 `lg_react_support.py`，不要把这类纯 helper 再塞回 `lg_react.py`。
- 如果只是改检索结果标准化、records 收口或文档片段字段筛选，优先改 `lg_retriever_support.py`，不要把这类纯 helper 再塞回 `lg_retrievers.py`。
- 如果 `lg_nodes.py` 再次明显变大，应优先继续拆执行器辅助函数，而不是把更多实现塞回 `lg_builder.py`。
