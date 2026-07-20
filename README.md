# AssistGen - 基于大语言模型构建的智能客服系统

基于 FastAPI + Vue 3 的前后端分离智能客服助手，支持 DeepSeek、Qwen2.5、Llama3 等多种大语言模型，覆盖 Agent、RAG、知识图谱在智能客服领域的主流应用场景。

## 功能特性

### 1. 通用问答 & 深度思考
- 支持 DeepSeek V3 / R1 在线 API
- 支持 Ollama 接入任意对话模型（Qwen2.5、Llama3 等）
- 通过 `CHAT_SERVICE` / `REASON_SERVICE` 环境变量灵活切换

### 2. 智能 Agent (LangGraph)
- 四层嵌套子图：主图（路由分发）→ RetrievalPlan → 执行器 / ReAct 子图 → Text2Cypher 子图
- 5 路检索策略：GRAPH_ONLY / RAG_ONLY / PARALLEL / GRAPH_THEN_RAG / AGENT_REACT
- Retriever 抽象接口（依赖倒置），策略模式（Cypher 生成），注册表模式（检索器管理）
- Prompt 注入 4 层防线：XML 隔离 + 结构化输出 + Guardrails + 写操作硬拦截
- 温度分级体系：Router 0.1 → Cypher 0.2 → ReAct 0.4 → General 0.7

### 3. 分层记忆系统（优先级模型 P0-P3）
- **Redis 短期记忆**：ZSET 滑动窗口 + MsgPack 多级 Zstd 压缩 + LLM 压缩摘要
- **MySQL 用户画像**：结构化画像 + 事实版本追踪 + Redis 缓存层
- **Milvus 长期记忆**：混合检索（向量 + BM25 + RRF）+ 记忆衰减 + 敏感信息过滤
- **记忆优先级**：P0 最近消息 > P1 用户画像 > P2 会话摘要 > P3 长期记忆

### 4. Neo4j 知识图谱
- 电商知识图谱（8 节点 + 8 关系，16 个 CSV 初始化）
- 27 个预定义 Cypher 模板（bge-m3 语义匹配自动选择）
- Text2Cypher 动态生成（Few-Shot + 5 层验证 + 最多 3 次修正循环）
- 快速路径 + 兜底链路：PredefinedTemplateStrategy 命中模板，否则回退到 LLM 生成与校验链路

### 5. RAG 文档检索
- 支持 **Markdown（.md / .markdown）/ PDF / Word（.docx）** 上传 + 内置文档解析管道
- PDF 与 DOCX 先转为 Markdown，原生 Markdown 直接清洗分块后入库
- 混合检索（向量 + BM25 + RRF 融合）

### 6. 会话管理
- MySQL `conversations` 表只存会话元信息（标题、时间、类型）
- **消息不存 MySQL**，只保留在 Redis STM（ZSET 滑动窗口，24h TTL）
- 会话创建 / 列表 / 删除 / 改名
- **删除会话会联动清理记忆**：
  - MySQL：删除 `conversations` 元信息，并兼容清理历史 `messages` 表数据
  - Redis STM：删除该 `session_id` 下 messages/summary/meta/lock
  - Milvus LTM：软删除带 `session_id` 的长期记忆（`is_deleted=true`）

## 技术栈

- **后端**：FastAPI + SQLAlchemy (async) + LangGraph + LangChain
- **数据库**：MySQL 8.0 + Neo4j + Redis 7.0 + Milvus 2.6
- **LLM**：DeepSeek / Ollama（可切换）
- **Embedding**：bge-m3（1024 维）
- **前端**：Vue 3 + Element Plus + TypeScript

## 快速启动

### 1. 准备环境变量

先把根目录的环境变量模板复制到后端运行目录：

```bash
cp .env.example app/.env
```

然后只需要填写 API Key、模型配置等业务参数。

`.env.docker` 已内置容器网络下的 MySQL / Neo4j / Redis / Milvus 地址覆盖项，
也会自动覆盖 Compose 默认使用的数据库名、账号和密码，
不需要再把 `localhost` 或本地开发凭据手工改成容器服务配置。

### 2. Docker Compose 一键启动

```bash
docker compose up -d --build
```

启动流程会自动完成：
- MySQL / Neo4j / Redis / Milvus 基础设施启动
- `neo4j-importer` one-off job 会在检测到 `docker/neo4j-import/` 下存在完整 CSV 数据集时自动导入图谱；缺失时直接跳过，不阻塞启动
- `app` 服务启动前自动执行 MySQL 建表脚本
- `app` 通过 `.env.docker` 自动切换到容器内服务地址和默认凭据
- FastAPI 对外暴露 `http://localhost:8000`
- 只有 `app` 服务映射宿主机 `8000` 端口；MySQL / Neo4j / Redis / MinIO / Milvus 都只在 Compose 内部网络可见
- 持久化数据写入 Docker 命名卷，而不是项目目录下的 `docker_data/`
- 卷名固定为 `kefu_mysql_data`、`kefu_neo4j_data`、`kefu_redis_data`、`kefu_milvus_data` 等，和当前目录名解耦

### 3. 查看服务状态

```bash
docker compose ps
```

如果需要连同数据库和向量库数据一起清空：

```bash
docker compose down -v
```

如果只是查看当前命名卷：

```bash
docker volume ls | grep '^local.*kefu_'
```

如果后续要恢复 Neo4j 图谱初始化，把那 16 份 CSV 数据放进 `docker/neo4j-import/` 即可，无需再改 `compose`。

项目当前只保留 `docker compose` 这一种启动方式，不再保留其他应用启动入口。

### 4. 开发检查（可选）

根目录的 [pyproject.toml](/Volumes/移动卷宗/学习/Aiprogram/智能客服Agent/code/deepseek_agent/pyproject.toml) 已统一收敛了 `pytest` 和 `ruff` 的基础配置。

如果本机已安装这些工具，可直接执行：

```bash
pytest
ruff check app scripts tests
```

## 项目结构

当前项目以 `app/` 作为唯一主代码树。业务域（`chat` / `knowledge` / `user`）统一为：

```text
domain/ → application/ → infrastructure/
```

全局共享**只有** `app/shared`；对话域内工具在 `app/chat/infrastructure/utils`（禁止再命名 shared）。

```
deepseek_agent/
├── app/                         # 主应用目录
│   ├── api/                     # FastAPI 路由
│   ├── chat/                    # 对话 / Agent / KG（domain+application+infrastructure）
│   ├── knowledge/               # 记忆 / 文档解析 / 索引（同上骨架）
│   ├── user/                    # 用户与画像（同上骨架）
│   ├── shared/                  # 唯一全局共享内核
│   ├── platform/                # 应用容器
│   └── scripts/                 # Compose 内部脚本
├── configs/                     # Docker 初始化配置
├── specs/                       # 可跟踪设计文档
├── docs/                        # 本机详细文档（gitignore，见 modules 00–10）
├── scripts/                     # 仓库级辅助脚本
├── tests/                       # 与领域对齐的测试
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

### 架构特点

- **领域驱动设计**：按业务场景（chat/knowledge/user）划分领域，骨架一致
- **分层架构**：`domain` → `application` → `infrastructure`；`api` 只调 application
- **依赖倒置**：领域契约不依赖框架细节；基础设施可替换
- **单一主树**：旧兼容目录已移除；假 shared（chat 内）已改为 `utils`

## 相关文档

- [docs/全流程文档索引.md](docs/全流程文档索引.md) — **全流程详细文档索引（推荐，模块 00–10）**
- [docs/modules/00-全流程图集.md](docs/modules/00-全流程图集.md) — **Mermaid 全流程图集（强烈推荐）**
- [specs/2026-07-20-domain-skeleton-align-design.md](specs/2026-07-20-domain-skeleton-align-design.md) — 域骨架对齐设计
- [CHANGELOG.md](CHANGELOG.md) — 版本更新日志
- [app/README.md](app/README.md) — 当前主代码树说明
- [app/chat/README.md](app/chat/README.md) / [app/knowledge/README.md](app/knowledge/README.md) / [app/user/README.md](app/user/README.md) / [app/shared/README.md](app/shared/README.md)
- [docs/架构概览.md](docs/架构概览.md) — 当前架构和目录边界
- [docs/迁移指南.md](docs/迁移指南.md) — 新旧导入路径和迁移策略
- [docs/部署说明.md](docs/部署说明.md) — Docker Compose 部署方式
- [docs/开发规范.md](docs/开发规范.md) — 开发规范
- [app/scripts/README.md](app/scripts/README.md) — 应用内维护脚本说明
- [app/shared/core/README.md](app/shared/core/README.md) — 共享基础设施说明
- [app/shared/security/README.md](app/shared/security/README.md) — Prompt 防护说明
- [app/user/infrastructure/models/README.md](app/user/infrastructure/models/README.md) — 持久化模型说明
- [app/chat/infrastructure/kg/README.md](app/chat/infrastructure/kg/README.md) — KG 子图说明

## License

MIT
