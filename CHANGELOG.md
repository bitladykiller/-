# 更新日志

所有项目的显著变更都将记录在此文件中。

本文档遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
