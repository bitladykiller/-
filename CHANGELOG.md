# 更新日志

所有项目的显著变更都将记录在此文件中。

本文档遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Redis Stream 幂等消费

- 新增 MySQL `processed_events` Inbox：按 `(event_type,event_id)` 管理处理租约、
  payload hash、完成与失败状态；已完成消息重放仅 ACK。
- `turn_completed` 增加稳定 `turn_id`：MySQL messages 唯一键和 Redis STM 回合
  集合共同防止重复历史/上下文写入；LTM 使用事件派生的确定性主键 upsert。
- `document_index_requested` 复用 `task_id`：任务索引 chunk ID 稳定化，并以
  Milvus upsert 与已有任务版本检测抵抗 ACK 前重放。
- 新增 `configs/mysql-init/migration_stream_idempotency.sql`，同时更新 fresh
  install 的 `init.sql` 与系统模块文档。

## [v3.35.2] - 2026-07-26

第五轮审查：清剿残余的事件循环阻塞热点 + 注册竞态。

### 性能（事件循环阻塞，v3.33 专项的收尾）
- **Text2Cypher 三个异步节点内 6 处同步阻塞**：`predefined_match` 的
  语义匹配（同步 requests 调 Ollama embedding，超时可达 10s）、参数提取、
  模板执行 `graph.query`；`execute_cypher` 的查询 RTT；`validate_cypher`
  的 EXPLAIN 语法校验 / 方向纠正 / schema 校验——全部改经 `run_blocking`
  线程池。此前**每次 KG 查询都会把全部并发请求卡住**。
- **Reranker 精排下线程池**：CrossEncoder 同步 CPU 推理（首次含权重加载）
  曾直接跑在异步 `HybridSearcher.search` 里，每次精排阻塞数百毫秒。
- **首次请求冷构造下线程池**：`get_retriever` 首次注册链
  （Neo4jGraph 连接、27 模板 embedding、子图编译、Milvus 连接、
  embedding 权重加载）曾在协程内同步执行——首个提问用户会拖住所有人。
  两个注册器改 async + `run_blocking`。
- **启动预热检索器**：`AppContainer.warm_up` 新增 `_warm_retrievers`
  （尽力预建共享 HybridSearcher / KG 子图，失败降级记录不阻断启动）——
  冷构造成本从"首个用户买单"移到启动期。

### 修复
- **注册并发竞态**：两个同名注册同时穿过查重 SELECT 后，后提交者撞
  唯一键抛 IntegrityError 被翻成 500。现 rollback 后转
  RegistrationError（400「用户名已被占用」）——查重只是友好文案的
  快捷路径，真正的裁判是数据库唯一约束。

### 质量
- 测试 382 → 385：rerank 线程断言、三处冷构造线程断言、注册竞态回归。

## [v3.35.1] - 2026-07-26

第四轮审查：1 个静默连接故障 + 若干 v3.35 接缝修正。

### 修复
- **LTM 连的可能不是 Milvus 服务（第 4 个静默故障）**：`settings.MILVUS_URL`
  返回 `host:port` **不带 scheme**，而 pymilvus 把无 scheme 的 uri 按
  **本地文件路径**处理、转投 milvus-lite 嵌入式库——要么启动报错，要么
  （pymilvus 在 Linux 自带 milvus-lite 时）**静默连到容器内嵌入式本地库**：
  长期记忆看似工作，实际与真正的 Milvus 服务完全隔离，容器重建即清零。
  修复为 `http://host:port`（07 文档一直写的就是带 scheme 的正确形态）。
- **bootstrap 建表漏掉 messages 模型**：`db_script_support.prepare_db_models`
  未导入新的 Message 模型，脚本路径 `create_all` 不会建 messages 表
  （compose 走 init.sql 不受影响）。
- **429 前不再创建空会话**：限流检查移到会话解析之前——此前被限流的
  请求每次都会留下一个空会话行；归属校验失败也会正确释放并发槽位。
- **owner 常量单源**：上传/索引侧的共享域标识改读
  `rag_visibility.global_owner`，与检索过滤同源（消除两处硬编码 "global"）。

### 前端
- AuthPanel 重绘为「鎏金礼宾 Art Deco」设计语言（--gold/--ivory/--noir
  设计变量 + plaque/gold-btn 组件类），与 v3.35 合并进来的整体视觉一致。
- 任务状态类型补充 `interrupted` 字面量。

## [v3.35.0] - 2026-07-26

架构路线图整轮落地：鉴权、事件管线、消息持久化、评测、可观测性、
配置校验、知识分域一次到位。**包含大量破坏性 API 变更**，见文末。

### 1. 鉴权与信任边界（核心变更）
- 新增 `/api/auth/register`、`/api/auth/login`、`/api/auth/me`；
  JWT（HS256，`SECRET_KEY` 签名，TTL `ACCESS_TOKEN_TTL_SECONDS`）+ bcrypt 密码。
- **所有业务端点身份改由 Bearer 令牌推导**，路径/表单/请求体不再接受
  自报 `user_id` —— 此前前端 localStorage 一个数字就是"身份"。
- `users.password_hash` 列首次真正投入使用；种子用户密码统一为
  `demo1234`（生产请删除种子用户）。
- 前端新增登录/注册门（AuthPanel），token 注入所有请求，401 自动回登录页。

### 2. 会话标识唯一化
- `/api/langgraph/query` 的 `conversation_id` 改为 int 且**必须属于当前用户**
  （不传则服务端自动建会话）；`thread_id ≡ str(conversation_id)`。
- 消除"任意字符串 thread 产生孤儿 STM/LTM、会话删除清不掉"的隐患。

### 3. 消息持久化（告别"隔天失忆"）
- 启用 init.sql 中一直存在但从未写入的 `messages` 表（append-only 历史）。
- 新增 `GET /api/conversations/{id}/messages`；前端切回旧会话自动加载历史。
- 与 Redis STM 职责分离：历史给人看，STM 是模型的上下文窗口。

### 4+5. Redis Streams 事件管线（任务持久化 + 记忆事件化）
- 新增 `app/shared/streams.py`：消费组 + XAUTOCLAIM 崩溃认领 + 重试上限 +
  死信流；零新依赖（redis.asyncio 已有）。
- `turn_completed` 事件：历史落库 + 记忆写入（压缩/抽取），崩溃自动重放；
  fire-and-forget 协程降级为事件基础设施不可用时的回退路径。
- 文档索引经 `document_index_requested` 事件执行：**进程重启后任务自动
  续跑**（此前只能标 interrupted）；任务状态协议不变，前端轮询无感知。
- 新增 `python -m app.worker` 独立消费入口 + compose 可选 worker 服务；
  app 进程默认内嵌消费（`EVENTS_INLINE_CONSUMER=0` 可关闭）。

### 6. RAG 离线评测
- `scripts/golden_set.jsonl`（24 条客服问题）+ `scripts/rag_eval.py`
  （hit@k / MRR，`--json` 可存档对比）+ `make eval`。
- 指标层为纯函数（`app/knowledge/application/rag_eval.py`），单测覆盖。

### 7. LLM 韧性与资源护栏
- 模型工厂统一挂接**按角色超时**（决策类 10s / Cypher 20s / 生成类 60s）
  与瞬时重试（1 次）；此前无任何超时，上游挂起请求会无限等待。
- SSE 按用户并发限流（默认 3 路，Redis 计数器 + TTL 兜底），超限 429；
  限流器自身故障放行（护栏不带走主功能）。
- SSE 结束时记录本次对话 token 用量（模型上报 usage 时）。

### 8. 可观测性
- `X-Request-ID` 贯穿：中间件生成/透传，contextvars + 日志 Filter 注入
  **每一条**日志（格式新增 request_id 列），响应头回传。
- 图节点耗时打点（`app.chat.graph.timing`）：路由/检索/生成各花多久直接可读。
- 新增 `GET /health/deep`：逐依赖探测（MySQL/Redis/Milvus/Neo4j），
  故障返回 503 + 组件明细；`/health` 保持浅探针。

### 9. 配置体系
- `AppConfig` 全树 dataclass → **pydantic frozen BaseModel**：启动时校验
  类型与取值范围（`extra="forbid"` 拒绝拼错的字段名）。
- LTM collection 名单源化：env `MILVUS_COLLECTION_NAME` 成为
  `app_config.memory.ltm.collection_name` 的默认值来源，容器只读后者。

### 10. 知识库分域（特性开关，默认关）
- chunk schema 新增 `owner_id`（"global" 或上传者 user_id）；
  上传支持 `visibility=global|private`。
- `rag_visibility.enabled=true` 时检索按 `owner_id in ("global", 当前用户)`
  过滤（身份取自请求上下文 contextvars，检索层零传参）。
- ⚠️ 开启前必须全量 reindex：存量 chunk 无 owner_id 字段会被过滤排除。

### 破坏性变更（API）
| 旧 | 新 |
|---|---|
| （无鉴权） | 除 /health、/api/auth/* 外全部需要 `Authorization: Bearer` |
| `GET /api/conversations/user/{uid}` | `GET /api/conversations` |
| `POST /api/conversations` body{user_id} | `POST /api/conversations`（空 body） |
| `DELETE /api/conversations/{id}?user_id=` | `DELETE /api/conversations/{id}` |
| `PUT .../name` body{user_id,name} | body{name} |
| `GET/DELETE /api/documents/user/{uid}/{doc}` | `GET/DELETE /api/documents/{doc}` |
| `POST /api/upload` form user_id | 身份取自令牌；新增可选 form `visibility` |
| `POST /api/langgraph/query` form user_id + 任意字符串会话 | 身份取自令牌；`conversation_id` 为 int 且校验归属 |

### 质量
- 测试 337 → **375**；ruff / mypy / vue-tsc 全绿。
- 新增测试：auth（哈希/令牌/伪造拒绝）、streams（重试/认领/死信）、
  events、rate_limit、rag_eval、分域过滤、会话统一。

## [v3.34.0] - 2026-07-26

第二轮设计审查的产出：2 个部署级静默故障 + API 完整性补全。

### 修复（静默故障）
- **STM 短期记忆写得进、读不出**：消息经 `compress_message` 压缩成二进制
  MsgPack/Zstd（非法 UTF-8），而 STM Redis 客户端开着 `decode_responses=True`——
  `zrevrange` 读取时严格 UTF-8 解码抛 `UnicodeDecodeError`，被降级逻辑吞掉，
  表现为**每轮对话拿到的最近消息恒为空**。新增 `create_stm_redis_client()`
  二进制安全工厂（`decode_responses=False`），读路径本就原生支持 bytes。
- **上传文件写在持久卷外面**：`UPLOAD_DIR` 曾是相对路径，随 CWD 落到
  `/app/uploads`（容器可写层），而卷挂在 `/app/app/uploads` —— 容器重建即丢失
  全部上传原文件（Milvus 索引与 MySQL 元数据却还在）。改为环境变量
  `UPLOAD_DIR` 可配 + 启动时 resolve 为绝对路径；`.env.docker` 已对齐卷挂载点。

### 安全 / API 完整性
- **会话删除/改名补归属校验（修 IDOR）**：`DELETE /api/conversations/{id}`
  必须带 `user_id`（query），`PUT .../name` 请求体加 `user_id`；
  不存在与不属于当前用户统一返回 404（防资源 ID 枚举）。
  删除会联动清空该会话 STM/LTM 记忆，此前任何人可按 id 裸删。
- **新增 `DELETE /api/documents/user/{user_id}/{doc_id}`**：删除知识文档
  （MySQL 删元信息行 + Milvus 软删该 doc_id 全部 chunk）。此前只有上传/替换、
  没有删除入口，传错的文档会永远留在检索库里污染答案。
- **404 语义修复**：新增 `app/shared/core/errors.ResourceNotFoundError`，
  `run_api_action` 统一映射 404。此前 Repository 抛裸 ValueError 被翻成 500，
  前端无法区分"资源没了"和"服务器坏了"。

### 体验
- **记忆写入移出 SSE 关键路径**：`after_response` 改为 fire-and-forget 后台任务
  （带引用跟踪 + `flush_pending_memory_writes()`）。此前触发压缩的轮次，
  用户看完答案后连接还要挂着等"摘要 LLM + 抽取 LLM"数秒才关闭。
- **SSE 流中途异常不再静默断流**：generator 内捕获异常并发出
  `event: error` 命名事件；前端解析器同步升级（保留已生成内容 + 展示错误）。

### 部署与工程卫生
- Dockerfile 基础镜像 `python:3.12-slim` → **`python:3.10-slim`**，
  与本地/CI 测试解释器对齐（此前生产跑在从未测过的版本上）。
- **依赖单一来源**：pyproject `dependencies` 改为 dynamic，指向
  `requirements.txt`（Docker 与 `pip install -e .` 共用一份，消除已发生的漂移）。
- CORS 移除无效的 `allow_credentials=True`（规范禁止与通配符 origin 组合）。
- 版本号补齐至 3.34.0。

### 语义澄清
- **知识库是全局共享的**：`user_documents` 的 user_id 只用于**上传管理归属**
  （谁能替换/删除），检索面向所有用户（Milvus chunk 无 user 维度）。
  文档与 README 已明确此定位；如需用户私有文档域，见 05 文档中的演进说明。

### API 变更（破坏性）
- `DELETE /api/conversations/{id}` 新增必填 query `user_id`
- `PUT /api/conversations/{id}/name` 请求体新增必填 `user_id`
- 会话/文档"不存在或不属于当前用户"由 500 → **404**

## [v3.33.0] - 2026-07-26

本次以「性能 / 可读性 / 架构」为目标做专项治理，过程中发现并修复了 3 个静默故障。

### 修复（静默故障，无报错但功能失效）
- **BM25 稀疏检索基本失效**：客户端用 `abs(hash(token)) % 2**24` 自行计算 token id。
  一来 Python 字符串 hash 默认按进程随机化（同一个词每次启动 id 都不同），
  二来 doc 侧 `sparse_vector` 由 Milvus 服务端 BM25 `Function` 用自己的词表生成，
  客户端无从得知其编号。两边落在不同 id 空间 → 稀疏分支几乎召不回，
  RRF 静默退化为纯向量检索。**改为把原始查询文本交给 Milvus**，由同一个 Function 编码。
- **长期记忆整条链路失效**：`ltm.search / deduplication / update_on_hit` 三个配置是
  `frozen dataclass`，代码却按 dict 下标取值，每次调用抛 `TypeError` 后被宽泛
  `except Exception` 吞掉 —— 表现为 LTM 永远检索不到、也永远写不进去。
  改为属性访问；原先的 `# type: ignore` 恰好压制了 mypy 对此的正确报错。
- **Docker 下 RAG 连不上 Milvus**：`RetrievalConfig` 硬编码 `localhost:19530`，
  而 `.env.docker` 是 `MILVUS_HOST=milvus`。LTM 走 `settings.MILVUS_URL` 连得上，
  RAG 走本类默认值连的是容器自己的 localhost。连接参数改为默认取自 `settings`。

### 性能
- **修复事件循环阻塞**：pymilvus 与 LangChain Embeddings 均为同步 SDK，此前在
  `async def` 内直接调用（全项目 0 处 `to_thread`），一次 LTM 检索会卡住整个进程的
  所有并发请求。新增 `app/shared/core/async_bridge.py`，所有数据面调用改走线程池。
- **文档索引批量化**：`insert_chunks` 由逐 chunk `embed_query` 改为批量
  `embed_documents`，N 次模型前向 / HTTP 往返降为 1 次。
- **记忆读取并发化**：`before_agent` 的 STM / 画像 / LTM 三路互不依赖，改 `asyncio.gather`，
  耗时由三者之和降为三者最大值；`compress_session_memory` 四路状态读取同样并发。
- **Redis 往返合并**：`append_message` 由 4 次往返合并为 1 次 pipeline；
  压缩重写窗口由 4N 次降为 1 次（保留 5 条即 21 → 1）；新增 `append_messages` 批量接口。
- **热对象复用**：`HybridSearcher` 与 embedding 模型改为进程内共享单例
  （此前每上传一个文档就新建 Milvus 连接并重新加载模型权重）。
- LTM 命中统计由逐条 upsert 改批量；`after_agent` 复用 meta 省一次往返。

### 架构与可读性
- 新增 `app/shared/core/degradation.py` 统一降级日志约定：外部依赖故障记 `warning`
  不打堆栈；其余一律 `logger.exception` 带完整堆栈。此前 75 处 `except Exception`
  把「外部抖动」和「代码缺陷」归为一类，22 处静默返回空值。
- 新增 `app/shared/core/embeddings.py` 作为 embedding 唯一构造入口，消除
  LTM 读 `settings` / RAG 读 `os.getenv` 的双真相来源。
- `simple_long_term_memory.py` 去掉「把 logger、写入函数等恒定依赖当参数层层下传」的
  伪注入（11 参数签名、6 处 type ignore、参数名遮蔽模块函数），收敛为类方法。
- `AppContainer` 新增 `KnowledgeGraphComponents`，`retriever_runtime` 不再读写容器
  下划线私有字段；检索器注册表初始化收进锁内，消除并发竞态。
- **`app/shared/task_queue.py` 更名 `background_tasks.py`**：它不是分布式队列，
  任务协程跑在进程内存里。新增 `worker_id` 与 `reconcile_orphaned_tasks()`，
  启动时把上一代进程遗留的 pending/running 收敛为 `interrupted`
  （新增该状态），避免前端永远轮询一个不会完成的任务。
- 删除 `IndexingService` 中为测试替身而存在的签名嗅探；上传模式校验收敛为
  `normalize_upload_mode` 单一实现；`decision_nodes._normalize_mode` 更名
  `_normalize_retrieval_mode`（与上传模式区分，此前同名不同义）。
- 删除仅测试引用的 `close_task_manager`；`main.py` `__all__` 从 12 个内部装配函数
  收敛为 `app` / `create_app`。

### 新增配置
- `RetrievalConfig.bm25_drop_ratio`（默认 `0.2`）：BM25 检索丢弃低权重项的比例
- `RetrievalConfig.milvus_host / milvus_port` 默认值改为取自 `settings`

### 破坏性变更
- `app.shared.task_queue` → `app.shared.background_tasks`
- `ltm_collection` 的 `insert_records / upsert_records / search_records` 改为协程
- `MilvusStore.get_max_version / soft_delete_by_doc_id / hard_purge_soft_deleted`
  与 `HybridSearcher.hard_purge_soft_deleted` 改为协程
- `MilvusHybridSearchCore.encode_query_sparse` 删除，由 `build_sparse_request` 取代
- `MemoryMiddleware._warn_once` → `_degrade_once`（签名带异常对象）

### 质量
- 测试 236 → 322，改动模块覆盖率均 ≥ 82%
  （`async_bridge` / `degradation` / `embeddings` / `hybrid_search` 达 100%）
- mypy 从改动前基线 8 个错误降为 **0**
- 并发与批量的断言均已验证可捕获退化实现（改回串行 / 逐条会 fail）

### 涉及文件
- 新增：`app/shared/core/async_bridge.py`、`degradation.py`、`embeddings.py`
- 重命名：`app/shared/task_queue.py` → `app/shared/background_tasks.py`
- 主要改动：`app/shared/retrieval/milvus_hybrid_core.py`、
  `app/knowledge/infrastructure/{ltm,stm,orchestration,doc_parser/retrieval}/*`、
  `app/platform/container.py`、`app/chat/infrastructure/retrievers/retriever_runtime.py`

## [v3.32.0] - 2026-07-21
### 新增
- **RAG 书面化查询改写**（仅文档检索支路）
  - `formalize_rag_query`：口语 → 政策/说明书风格检索问句
  - 挂载于 `MilvusDocRetriever.search`（含 ReAct `rag_search`）
  - 配置：`AppConfig.rag_rewrite`（默认开启，超时 3s 回退）
  - **不做** HYDE / 退步改写
  - 图谱 `KnowledgeGraphRetriever` 不改写

### 涉及文件
- `app/chat/infrastructure/retrievers/rag_query_formalize.py`
- `app/chat/infrastructure/retrievers/retriever_implementations.py`
- `app/shared/core/app_config.py`
- `tests/chat/test_rag_query_formalize.py`、`test_lg_retrievers.py`

## [v3.31.1] - 2026-07-21
### 改进
- **replace 幂等**：更新文档时比对 MySQL `content_hash`，一致则跳过 reindex（不提交任务、不软删）
- 上传响应增加 `unchanged` / `skipped`；前端更新流程识别后直接提示完成

## [v3.31.0] - 2026-07-21
### 新增
- **MySQL `user_documents` 用户文档元信息表**
  - 稳定 `doc_id` ↔ 展示名/原始文件名/版本/状态/hash
  - API：`GET /api/documents/user/{user_id}`、`GET /api/documents/user/{user_id}/{doc_id}`
  - 上传 `create/replace` 前写元数据，索引完成后回写 `ready|failed` + version/chunks
- **前端知识文档抽屉**
  - 标签「上传新文档」/「我的文档 / 更新」
  - 列表展示 doc_id、version、status；「更新文档」固定绑定该行 doc_id 调 `mode=replace`
- 增量迁移脚本：`configs/mysql-init/migration_user_documents.sql`

### 涉及文件
- `app/knowledge/infrastructure/models/user_document.py`
- `app/knowledge/infrastructure/repository/user_document_repository.py`
- `app/knowledge/application/document_service.py` / `document_indexing_job.py`
- `app/api/documents.py` / `app/api/upload.py`
- `frontend/src/components/UploadDrawer.vue` / `api/client.ts`
- `configs/mysql-init/init.sql`、`migration_user_documents.sql`

## [v3.30.0] - 2026-07-21
### 新增
- **RAG 文档动态更新（策略 2：软删除 + version）**
  - Milvus schema：`is_deleted` / `version` / `updated_at` / `content_hash`
  - `soft_delete_by_doc_id` → 检索默认 `is_deleted == false`
  - `reindex_document`：软删旧版后写入下一 version（近零空窗、可审计）
  - `hard_purge_soft_deleted`：物理清理过期软删 chunk
  - 上传 API：`mode=create|replace` + 可选稳定 `doc_id`
  - `IndexingService` 编排 replace / create

### 注意
- **新建** collection 自动带版本字段；若已有旧 `rag_documents` 无上述字段，需重建 collection 或全量 reindex 后再依赖软删过滤

### 涉及文件
- `app/knowledge/infrastructure/doc_parser/retrieval/doc_lifecycle.py`
- `app/knowledge/infrastructure/doc_parser/retrieval/milvus_store.py`
- `app/knowledge/infrastructure/doc_parser/retrieval/hybrid_search.py`
- `app/knowledge/application/indexing_service.py` / `indexing_contracts.py`
- `app/api/upload.py`
- `tests/knowledge/test_doc_lifecycle.py` / `test_milvus_store_lifecycle.py` / `test_indexing_service.py`

## [v3.29.1] - 2026-07-21
### 移除
- 删除旧版内嵌静态页 `app/static/dist`（前后端分离后仅保留 `frontend/` + Docker Nginx）

## [v3.29.0] - 2026-07-21
### 新增
- **前后端分离工程化前端** `frontend/`
  - Vue 3 + Vite + TypeScript + Pinia + Vue Router
  - 编辑感深色控制台 UI（会话 / SSE / 上传）
  - 多阶段 Docker 构建 + Nginx 反代 `/api` `/health` `/docs`
  - `docker-compose` 服务 `frontend` 映射 `8080:80`
- CORS 暴露 `X-Conversation-ID`（便于跨域调试）

## [v3.27.0] - 2026-07-21
### 新增
- LTM **定时硬清理**：软删且超过保留期的记录从 Milvus 物理删除
  - `SimpleLongTermMemory.hard_purge_soft_deleted`
  - 后台循环 `purge_scheduler.run_ltm_hard_purge_loop`
  - `AppContainer.start_background_jobs` 在 lifespan 启动，关闭时取消
  - 配置：`AppConfig.memory.ltm.purge`（interval 默认 1h，retention 默认 7 天）

### 涉及文件
- `app/shared/core/app_config.py`
- `app/knowledge/infrastructure/ltm/simple_long_term_memory.py`
- `app/knowledge/infrastructure/ltm/purge_scheduler.py`
- `app/platform/container.py`、`app/main.py`
- `tests/knowledge/test_ltm_hard_purge.py`

## [v3.26.0] - 2026-07-21
### 改进
- **检索计划方案 A**：由互斥五选一改为能力标签编排
  - LLM 输出 `need_graph` / `need_rag` / `mode(single|parallel|sequential)` / `complexity(simple|multi_hop)`
  - 代码 `resolve_execution_plan` 解析为执行路径：GRAPH_ONLY / RAG_ONLY / PARALLEL / GRAPH_THEN_RAG / AGENT_REACT
  - 边路由使用 `resolved_plan`；缺失则 REACT 兜底

### 涉及文件
- `app/chat/infrastructure/graph/state.py`
- `app/chat/infrastructure/graph/decision_nodes.py`
- `app/chat/infrastructure/modeling/models.py`、`prompts.py`
- `tests/chat/test_lg_nodes.py`

## [v3.24.1] - 2026-07-20
### 文档
- 澄清本仓库为**后端 only**（前端不在 monorepo）
- 文档索引补「能力覆盖地图」与诚实边界（鉴权/SerpAPI/handoff）
- 场景 D 上传类型与 `completed` 状态与代码对齐
- 在现有 03–08 等模块内扩写「关键函数」详解（不另增文档编号）

## [v3.24.0] - 2026-07-20
### 改进
- 预定义 Cypher 匹配：用 `cosine_similarity_score`（NumPy）替代 `sklearn.metrics.pairwise.cosine_similarity`
  - 类型对 Pylance/basedpyright 友好
  - 零向量（embedding 失败降级）稳定返回 `0.0`，避免 nan
- 静态检查：新增/调整 `pyrightconfig.json`，**仅检查 `app/`**，排除 `tests/`（与 mypy `tests.* ignore_errors` 对齐）
- 清理 app 侧 Pylance 泛型/类型诊断；mypy / ruff / pytest 保持通过

### 涉及文件
- `app/chat/infrastructure/kg/predefined_cypher/utils.py`
- `tests/chat/test_predefined_cypher_utils.py`
- `pyrightconfig.json`

## [v3.23.0] - 2026-07-20
### 改进
- 业务域骨架对齐（方案 A）：`chat` / `knowledge` / `user` 统一 `domain` + `application` + `infrastructure`
- 消灭假 shared：`app.chat.infrastructure.shared` → `app.chat.infrastructure.utils`
- 新增 `chat.domain`；各域与 `app.shared` README 明确边界
- 设计说明：`specs/2026-07-20-domain-skeleton-align-design.md`

### 涉及文件
- `app/chat/infrastructure/utils/`
- `app/chat/domain/`
- `app/chat/README.md`、`app/knowledge/README.md`、`app/user/README.md`、`app/shared/README.md`、`app/README.md`

## [v3.22.0] - 2026-07-20
### 改进
- 文档上传与索引支持三种类型：**Markdown（.md / .markdown）、PDF、Word（.docx）**
- 新增 `MarkdownFileParser`：原生 Markdown 直读后进入与 PDF/DOCX 相同的清洗与分块管线
- `IndexingService` / `POST /upload` 允许扩展名与解析管线对齐

### 涉及文件
- `app/knowledge/application/indexing_service.py`
- `app/api/upload.py`
- `app/knowledge/infrastructure/doc_parser/pipeline.py`
- `app/knowledge/infrastructure/doc_parser/parsers/markdown_parser.py`
- `app/knowledge/infrastructure/doc_parser/exceptions.py`

## [v3.21.0] - 2026-07-19
### 改进
- `DELETE /api/conversations/{conversation_id}` 删除会话时联动清理记忆：
  - Redis STM：清空该会话 messages/summary/meta/lock
  - Milvus LTM：按 `session_id` 软删除长期记忆
  - MySQL：删除会话元信息，并兼容清理历史 `messages` 表
- LTM 写入新增 `session_id` 字段，便于会话级清理

### 涉及文件
- `app/chat/application/conversation_service.py`
- `app/chat/infrastructure/repository/conversation_repository.py`
- `app/knowledge/infrastructure/stm/redis_short_term_memory.py`
- `app/knowledge/infrastructure/ltm/simple_long_term_memory.py`
- `app/knowledge/domain/schemas.py`

## [v3.20.0] - 2026-06-13
### 新增
- 完善异常日志记录，所有异常捕获都有 debug 级别日志
- 新增 `pyproject.toml` 完整配置 (项目元数据、依赖管理、工具配置)
- 新增 `.pre-commit-config.yaml` (代码质量自动检查)
- 新增 `.editorconfig` (编辑器统一配置)
- 新增 `Makefile` (常用命令脚本)
- 新增 `CONTRIBUTING.md` (贡献指南)

### 改进
- redis_short_term_memory: 4 处异常捕获增加日志
- memory_extractor: LLM 响应解析失败时记录日志
- memory_extractor_support: JSON 解析失败时记录日志
- stm_store_utils: 消息解压失败时记录日志

## [v3.19.0] - 2026-06-13
### 改进
- 消除 6 处静默异常 (`except Exception: pass`)
- 所有异常记录日志，保留异常上下文和操作信息
- 不改变原有降级行为，仅增加可观测性

### 涉及文件
- `memory_middleware.py`: 用户画像更新失败时记录日志
- `redis_short_term_memory.py`: 5 处操作失败时记录日志

## [v3.18.0] - 2026-06-13
### 新增
- **模块化重构**: lg_agent 按职责划分为 graph/retrieval/react/memory_bridge/modeling 五大子模块
- **记忆系统重构**: memory 按层次拆分为 config/stm/ltm/profile/orchestration 五个子域
- **Facade 层**: lg_agent/facade.py 与 memory/facade.py 提供统一外部接口
- **运行时分离**: 区分 runtime(运行时依赖) / support(纯函数辅助) / utils(通用工具)
- **文档完善**: 每个包下新增 README.md 说明边界与职责
- **测试补充**: 新增 70+ 测试文件覆盖核心逻辑

### 移除
- 删除未使用的 customer_tools / text2cypher models / regex_patterns
- 清理重复代码和冗余导入

### 改进
- 配置外置: Prompt 迁移至 lg_prompts.yaml，支持热更新
- Docker 增强: .env.docker 覆盖容器网络，Dockerfile 多阶段构建
- Git 规范: .dockerignore 过滤构建产物，neo4j-import.sh 增加幂等性检查

## [v3.17.0] - 2026-06-12
### 新增
- 外部化 Prompt 至 YAML 文件
- 新增测试框架配置

### 修复
- 修复 react_subgraph NameError
- 修复 _LazyModel dunders
- 修复 memory 去重逻辑
- 修复 Config 代理简化

### 移除
- 删除图片上传功能
- 删除 VISION 配置
- 删除重复路由
- 清理 10+ 死代码文件

## [v3.9] - 代码质量优化 + 架构清理
### 移除
- 删除每次对话后自动写 MySQL 的 save_message 逻辑（减少 MySQL QPS）
- 移除 BaseLLMService / DeepseekService / OllamaService 中的 on_complete 回调参数
- 移除未使用的归档方法 archive_conversation

### Bug 修复
- 修复 ConversationService 四个方法的日志复制粘贴错误（方法名与变量不一致）
- 修复 upload_image 端点缺少文件大小限制

### 安全加固
- 文件上传新增魔数签名验证（拒绝扩展名与内容不匹配的文件）
- LLMFactory 单例添加 threading.Lock 双重检查锁，保证线程安全

### 性能优化
- hybrid_search 移除冗余的独立向量预检索（混合检索成功时省一次 Milvus 查询）
- search_service 流式响应格式统一为结构化 JSON

### 代码整洁
- 全局统一使用 from app.core.logger import get_logger
- 删除未使用 import：LONG_TERM_MEMORY_TYPES、TOOL_DEFINITIONS、format_search_context
- 清理全部 __pycache__/ 目录

## [v3.8] - 多级 Zstd 压缩 + Agent 架构重构
### 新增
- MsgPack + 多级 Zstd 压缩（按消息大小自动选择压缩级别）
- LTM 混合检索从手动 BM25 迁移到 Milvus 内置 BM25 Function
- hybrid_search 降级机制（BM25 失败 → 纯向量检索）

## [v3.7] - LangGraph 多图架构 + 分层记忆
### 新增
- LangGraph 三层嵌套子图（主图 → KG 子图 → Text2Cypher 子图）
- Redis 短期记忆（ZSET 滑动窗口 + LLM 压缩）
- Milvus 长期记忆（混合检索 + 记忆衰减 + 去重 + 敏感信息过滤）
- MySQL 用户画像（版本追踪 + Redis 缓存层）
- 5 层 Cypher 验证链
- RetrievalPlan 5 路路由器
- Prompt 注入 4 层防线

### 移除
- GraphRAG → rag_doc_parser
- MemorySaver（对话连续性由 Redis STM + Milvus LTM 保证）

## [v3.6] - Agent 架构完善
### 重构
- LLMFactory 单例模式
- 温度体系分级（0.1 / 0.2 / 0.7）
- Neo4j 连接缓存 + RETURN 1 探活

## [v3.0] - AssistGen
### 新增
- DeepSeek Function Calling 工具调用
- 用户历史会话管理（创建 / 删除 / 改名）
- Redis 上下文缓存管理
- init_db.py 脚本异步运行问题修复

## [v2.0] - AssistGen Ch 2.1 ~ 2.5
### 新增
- FastAPI + MySQL 接入
- 用户注册 / 登入 / 登出
- DeepSeek V3 / Ollama 流式问答
- DeepSeek R1 深度思考流式问答
- Serper API 联网检索
- sentence-transformers 本地知识库问答

## [v1.0] - AssistGen Ch 1.1 ~ 1.6
### 新增
- Ollama 本地部署 + REST API
- DeepSeek V3 / R1 在线 API 接入
