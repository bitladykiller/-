# Services 模块说明

`llm_backend/app/services/` 负责承载可复用的业务服务。这里的职责不是提供 HTTP 路由，也不是直接参与 Agent 图编排，而是把“可独立调用的业务流程”收敛成稳定接口。

## 结构分工

- `conversation_service.py`
  - 负责会话的创建、列表查询、删除和重命名。
  - 面向 `conversations` 表，不处理 FastAPI 请求对象。
  - 当前主文件只保留对外服务入口和动作编排；session 生命周期、单条会话查找、CRUD helper、会话摘要序列化、默认对象构造、列表查询语句已下沉到 `conversation_support.py`。
- `conversation_support.py`
  - 负责 `ConversationService` 共享的会话摘要序列化、默认会话对象构造、列表查询语句，以及 CRUD 会复用的数据库 helper / session 样板。
  - 这样 `conversation_service.py` 不再同时背负返回结构转换、SQL 语句样板和一串数据库私有 helper。
- `indexing_service.py`
  - 负责文档解析、切分和索引写入。
  - 是上传接口与 `rag_doc_parser` 之间的业务编排层。
  - 当前最小输入契约是 `path + user_id`，由上传接口负责补齐文件落盘路径。
  - 当前已支持注入解析管道和 doc_id 生成器，便于测试主流程而不依赖 `rag_doc_parser` 实际安装。
  - 源文件解析/校验、结果构造和依赖加载样板已下沉到 `indexing_support.py`，主文件只保留“解析 -> 索引”的服务编排。
- `indexing_support.py`
  - 负责 `IndexingService` 共享的轻量类型契约、源文件规范化/校验、doc_id 构造、降级结果构造和延迟依赖加载。
  - 这样 `indexing_service.py` 不再同时背负业务编排和输入规整细节。
- `document_formats.py`
  - 负责维护上传接口与索引服务共享的文件格式契约。
  - 当前能力边界跟随 `rag_doc_parser`，避免出现“API 允许上传，但后台其实不能索引”的配置漂移。
- `task_queue.py`
  - 负责异步任务提交、状态流转和 Redis 中的任务状态存储。
  - 当前只承担轻量后台任务管理，不做复杂调度系统。
  - 当前主文件聚焦在“提交 / 运行 / 查询 / 关闭”主流程；运行时单例生命周期已下沉到 `task_queue_runtime.py`。
  - Redis 状态载荷编解码、task key / id 生成这类纯逻辑已下沉到 `task_queue_utils.py`。
  - Redis client 构造、后台任务命名、状态落库这类通用 helper 已下沉到 `task_queue_support.py`。
- `task_queue_runtime.py`
  - 负责 `TaskManager` 运行时单例的懒创建、全局引用切换和安全关闭。
  - 这样 `task_queue.py` 不再同时承载业务执行和应用生命周期管理细节。
- `task_queue_support.py`
  - 负责 `TaskManager` 主流程会复用的轻量 helper。
  - 当前承接 TaskStore 协议、Redis client 构造、任务命名、统一状态读写，以及后台任务状态流转样板，避免这些细节污染服务主流程。
- `user_profile_service.py`
  - 负责用户画像和用户事实的对外服务入口。
  - 当前主线只保留“读取画像 / 批量回写画像”的对外服务入口，以及 cache/store helper 的调度。
  - 对外主入口仍然是“读取画像”和“批量回写结构化画像”，上层模块不需要感知底层 MySQL 细节。
- `user_profile_service_support.py`
  - 负责 `user_profile_service.py` 共享的服务编排 helper。
  - 当前承接缓存 key 生成、Redis 缓存读写、缓存失效和事务提交样板。
  - 这样 `user_profile_service.py` 不再同时背负对外服务入口和一串缓存/事务细节。
- `user_profile_store.py`
  - 负责 `user_profiles` / `user_facts` 的 MySQL 读写细节。
  - 当前主文件聚焦在查询主流程和事务内写库编排。
  - facts 版本链和画像字段 upsert 的数据库样板已下沉到 `user_profile_store_support.py`。
  - 数据库行合并、空画像构造、upsert 字段拼装这类纯 helper 已下沉到 `user_profile_store_utils.py`。
  - 这样服务层与数据访问层边界更清楚，后续如果要替换存储实现，不需要把缓存逻辑、SQL 编排、事实版本链和字段规整逻辑一起拆。
- `user_profile_store_support.py`
  - 负责 `user_profile_store.py` 共享的数据库 helper。
  - 当前承接画像行查询、facts 查询、facts 版本链更新和画像字段 upsert 执行样板。
  - 这样 `user_profile_store.py` 不再同时背负数据访问入口和一长串 SQL 细节。
- `user_profile_store_utils.py`
  - 负责 `user_profile_store.py` 共享的纯 helper。
  - 当前承接空画像构造、单行画像结果合并和 `user_profiles` upsert 参数拼装。
  - 这样 `user_profile_store.py` 不再同时背负数据库主流程和大量字段规整细节。

## 当前边界

- `services/` 只暴露可复用业务接口，不暴露路由细节。
- 服务层可以依赖数据库、Redis、索引模块，但不直接依赖 FastAPI 的请求/响应对象。
- Agent 相关主流程编排放在 `lg_agent/`，不要把 LangGraph 节点逻辑塞回服务层。

## 后续维护建议

- 如果某段逻辑已经被多个路由、多个中间件或多个 Agent 节点复用，优先考虑下沉到 `services/`。
- 如果某个服务文件开始同时处理“参数解析 + HTTP 状态码 + 数据访问 + 业务规则”，说明边界又混了，应把 HTTP 相关逻辑移回 `api/`。
- 如果某个服务文件开始同时容纳“主流程编排 + 大量源数据规范化 / 结果字典构造”，优先拆出 `*_support.py` 或等价 helper 模块。
- 如果某个服务文件开始同时容纳“业务主流程 + 运行时单例生命周期”，优先拆出 `*_runtime.py`，避免 shutdown 细节污染主业务类。
- 对于存在缓存的服务，优先把“缓存 key / 缓存失效 / 序列化格式”抽成 helper，避免主流程方法里散落重复细节。
- 如果某个服务文件开始同时容纳“Redis 缓存编排 + 大量 SQL + 行数据转换”，优先拆出 `*_store.py` 或等价数据访问模块。
