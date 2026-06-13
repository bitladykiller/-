# Memory 模块说明

`llm_backend/app/memory/` 负责 Agent 的会话记忆与长期记忆能力，设计目标是把“存储细节”“抽取逻辑”“编排流程”分开，避免单个文件同时承担太多职责。

当前这个目录已经开始从“平铺文件 + helper 文件”迁移到“按能力分包”的结构：

- `stm/`：Short-Term Memory，短期记忆入口
- `ltm/`：Long-Term Memory，长期记忆入口
- `profile/`：用户画像桥接与标准化入口
- `orchestration/`：记忆抽取与 before/after 编排入口
- `config/`：记忆静态配置入口

为了降低迁移风险，旧的平铺模块路径仍然保留为兼容入口，所以当前会同时看到：

1. 新的能力子包入口
2. 旧的平铺实现文件

后续新增代码应优先依赖新的能力子包路径，旧平铺路径只作为兼容层逐步收口。

## 结构分工

- `__init__.py`
  - 负责暴露记忆模块的稳定包级入口。
  - 当前把 Redis / Milvus 相关重依赖改成惰性导出，避免只导入轻量工具或纯函数时也触发完整依赖链。
- `config/`
  - 负责维护记忆域的静态配置入口。
  - `config/__init__.py` 暴露 TypedDict、accessor 和敏感规则编译 helper。
  - `config/defaults.py` 暴露默认配置常量。
- `memory_config_defaults.py`
  - 保留为旧默认配置路径的兼容入口。
- `stm/`
  - `stm/store.py` 暴露 Redis 短期记忆主入口。
  - `stm/compressor.py` 暴露消息压缩入口。
  - `stm/utils.py` 暴露 session key、摘要解析和消息窗口等纯 helper。
- `redis_short_term_memory.py`
  - 管理短期记忆的 Redis 读写。
  - 只关心消息窗口、摘要、元信息，不承担 LLM 抽取逻辑。
  - 当前内部已把 session key 构造、JSON→模型读写、消息批量解压、摘要解析等纯数据逻辑继续下沉到 `stm_store_utils.py`，压缩主流程只保留窗口与摘要编排。
- `stm_store_utils.py`
  - 存放短期记忆存储层的纯 helper，例如 session key 构造、Redis JSON 反序列化、消息窗口切分和摘要 JSON 提取。
  - 这样 `redis_short_term_memory.py` 可以更专注在 Redis I/O 和压缩流程，后续这部分也能单独补测试。
- `stm_compressor.py`
  - 负责 `MessageRecord` 的 MsgPack + Zstd 压缩与解压。
  - 让 Redis 层专注于存储，不混入压缩细节。
- `simple_long_term_memory.py`
  - 管理长期记忆的 Milvus 建表、写入、检索、去重和合并。
  - 依赖 `ltm_collection.py`、`ltm_utils.py`、`ltm_store_utils.py`、`ltm_operation_utils.py` 和 `ltm_runtime_support.py` 中的 helper，减少文件内的工具代码噪音。
  - 当前构造函数已支持注入 `retrieval_core`，让单测和后续替换检索实现时不必强绑默认 `MilvusHybridSearchCore`。
  - 当前已把“collection 初始化”“Milvus 检索调用样板”“去重检索”“待合并 cluster 加载”“搜索结果转换”“过滤表达式构造”“记录 payload 构造”“相似记忆聚类”“检索参数解析”“命中/软删/合并计划构造”拆出，主流程方法更聚焦在长期记忆编排。
- `ltm/`
  - `ltm/store.py` 暴露长期记忆主入口。
  - `ltm/collection.py` 暴露 collection schema / index / query helper。
  - `ltm/search.py` 暴露 dense / hybrid 检索运行时 helper。
  - `ltm/merge.py` 暴露合并与操作规划 helper。
  - `ltm/utils.py` 暴露记录构造、聚类和纯函数工具。
- `ltm_collection.py`
  - 存放长期记忆 collection 的 schema / index 定义和 Milvus client 调用样板。
  - 这样 `simple_long_term_memory.py` 不需要再直接展开 create/query/upsert/search 的底层 API 细节。
- `ltm_utils.py`
  - 存放长期记忆的纯函数工具，例如实体转换、相似度计算、内容合并。
- `ltm_store_utils.py`
  - 存放长期记忆存储层的纯 helper，例如过滤表达式、Milvus 记录构造、去重命中判断和聚类逻辑。
  - 这样可以在不引入 Milvus 客户端依赖的情况下直接为这部分逻辑补单测。
- `ltm_operation_utils.py`
  - 存放长期记忆“操作规划”层的纯 helper，例如检索参数解析、写入/命中/软删 payload、聚类合并计划和日志预览。
  - 这样 `simple_long_term_memory.py` 只保留 embedding 与 Milvus I/O 编排，不再夹杂大量参数和 payload 细节。
- `ltm_runtime_support.py`
  - 存放长期记忆运行时样板 helper，例如默认 `retrieval_core` 创建、collection 初始化、dense/hybrid 检索调用、去重检索和待合并 cluster 加载。
  - 这样 `simple_long_term_memory.py` 不再同时背负长期记忆编排和一串 Milvus 调用样板。
- `memory_extractor.py`
  - 负责把对话提炼成“长期记忆候选项”和“用户画像更新”。
- `memory_extractor_support.py`
  - 存放长期记忆抽取层的纯 helper，例如响应文本抽取、JSON 提取、敏感信息脱敏和语义记忆过滤。
  - 这样 `memory_extractor.py` 可以更聚焦在 prompt 编排和 LLM 调用，相关纯逻辑也能直接补单测。
- `profile_utils.py`
  - 负责用户画像的共享标准化规则。
  - 统一文本字段、标签、facts 的过滤与收口，避免同一套规则在抽取层、编排层、服务层各写一份。
  - 当前同时承载 payload 构造、tags JSON 解码和 facts 行转换，减少 `user_profile_service.py` 里的数据整理样板。
- `profile_gateway.py`
  - 负责 memory 层与用户画像服务层之间的桥接。
  - 当前统一承接 user_id 规范化、画像读取和画像回写默认入口，让 `memory_middleware.py` 不再在方法内部直接 import 服务层。
- `profile/`
  - `profile/gateway.py` 暴露画像服务桥接入口。
  - `profile/utils.py` 暴露画像标准化 helper。
  - `profile/cache.py` 暴露可供缓存层复用的画像收口入口。
- `memory_middleware.py`
  - 统一编排 `before_agent` / `after_agent` 两个阶段。
  - 负责决定什么时候读短期记忆、什么时候检索长期记忆、什么时候触发压缩与抽取。
  - 当前只会在 Redis STM 压缩真实成功后继续触发长期记忆抽取，不再把“达到压缩阈值”和“压缩已完成”混为一谈。
  - 当前主文件只保留阶段编排和降级策略；读取层、短期写入、摘要压缩回调、长期记忆落库和画像回写 helper 已继续下沉到 `memory_middleware_support.py`。
  - 结构化画像的字段/facts 落库细节统一下沉到 `user_profile_service.py`，服务调用通过 `profile_gateway.py` 注入默认桥接。
- `memory_middleware_support.py`
  - 负责 `memory_middleware.py` 共享的阶段 helper。
  - 当前承接短期记忆读取、画像读取、长期记忆读取、短期记忆写入、摘要压缩回调以及长期记忆/画像落库的具体调用样板。
  - 这样 `memory_middleware.py` 不再同时背负主流程编排和一串依赖型小函数。
- `orchestration/`
  - `orchestration/middleware.py` 暴露记忆中间件入口。
  - `orchestration/extractor.py` 暴露长期记忆抽取器入口。
  - `orchestration/runtime.py` 暴露编排阶段共用 helper 的兼容入口。
- `prompt_builder.py`
  - 只负责构造可注入 Prompt 的文本片段。
  - 当前同时承载长期记忆注入、会话摘要注入和短期记忆压缩提示词，避免这些纯文本模板散落在中间件里。

## 请求生命周期

### 1. `before_agent`

- 读取 Redis 中的最近消息和摘要。
- 读取 MySQL 用户画像。
- 从 Milvus 检索与当前问题相关的长期记忆。
- 将这些结果组装为 `AgentMemoryState`，交给上层 Agent 使用。

### 2. `after_agent`

- 把本轮用户消息和助手回复写入短期记忆。
- 根据轮次和消息数判断是否触发压缩。
- 压缩发生后，调用 `memory_extractor.py` 从对话中提炼可沉淀的长期记忆。
- 对本轮命中的长期记忆刷新命中信息。

## 当前拆分原则

- 新代码优先依赖能力子包路径，而不是继续扩散平铺模块依赖。
- 存储类不直接拼 Prompt。
- Prompt 构造器不直接访问 Redis / Milvus。
- 中间件只做编排，不实现底层存储细节。
- 纯函数尽量放进工具模块，方便后续单测和复用。

## 后续维护建议

- 如果新增短期记忆相关入口，优先放进 `stm/`。
- 如果新增长期记忆相关入口，优先放进 `ltm/`。
- 如果新增画像桥接和标准化入口，优先放进 `profile/`。
- 如果新增 before/after 编排入口，优先放进 `orchestration/`。
- 如果新增“存储格式”变化，优先放进 `stm_compressor.py` 或 `ltm_utils.py`。
- 如果新增“长期记忆的请求参数解析 / payload 规划”变化，优先改 `ltm_operation_utils.py`。
- 如果只是改 Milvus 检索调用样板、默认 `retrieval_core` 创建或待合并 cluster 加载，优先改 `ltm_runtime_support.py`。
- 如果新增“用户画像字段清洗 / facts 过滤”变化，优先改 `profile_utils.py`。
- 如果新增“请求阶段编排”变化，优先改 `memory_middleware.py`。
- 如果只是改 `before_agent/after_agent` 阶段里的具体读写样板，优先改 `memory_middleware_support.py`，不要把依赖调用细节重新堆回主中间件。
- 如果新增“提示词形态”变化，优先改 `prompt_builder.py`。
- 不要把业务场景判断重新塞回 `redis_short_term_memory.py` 或 `simple_long_term_memory.py`。
