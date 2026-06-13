# 更新日志

所有项目的显著变更都将记录在此文件中。

本文档遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
