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
- 28 个预定义 Cypher 模板（bge-m3 语义匹配自动选择）
- Text2Cypher 动态生成（Few-Shot + 5 层验证 + 最多 3 次修正循环）
- 策略模式：PredefinedTemplateStrategy → LLMGenerationStrategy

### 5. RAG 文档检索
- 支持 PDF / DOCX / TXT / CSV 上传 + rag_doc_parser 解析管道
- 混合检索（向量 + BM25 + RRF 融合）

### 6. 会话管理
- MySQL `conversations` 表只存会话元信息（标题、时间、类型）
- **消息不存 MySQL**，只保留在 Redis STM（ZSET 滑动窗口，24h TTL）
- 会话创建 / 列表 / 删除 / 改名

## 技术栈

- **后端**：FastAPI + SQLAlchemy (async) + LangGraph + LangChain
- **数据库**：MySQL 8.0 + Neo4j + Redis 7.0 + Milvus 2.6
- **LLM**：DeepSeek / Ollama（可切换）
- **Embedding**：bge-m3（1024 维）
- **前端**：Vue 3 + Element Plus + TypeScript

## 快速启动

### 1. 启动基础设施

```bash
docker-compose up -d
```

### 2. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 已包含 `shared_retrieval` 与 `rag_doc_parser` 的本地可编辑安装，无需额外手工安装兄弟模块。

### 3. 配置环境变量

复制 `.env.example` → `llm_backend/.env`，填写 API Key 和数据库连接信息。

### 4. 初始化数据库

```bash
python llm_backend/scripts/init_db.py
```

### 5. 导入 Neo4j 数据

```bash
bash neo4j-import.sh
```

### 6. 启动

```bash
python -m llm_backend.run
# 访问 http://localhost:8000
```

## 项目结构

```
deepseek_agent/
├── llm_backend/
│   ├── main.py                 # FastAPI 入口
│   ├── run.py                  # 启动脚本
│   ├── app/
│   │   ├── api/                # chat / conversations / upload / langgraph
│   │   ├── core/               # 配置 / MySQL 连接 / 日志
│   │   ├── lg_agent/           # LangGraph Agent
│   │   │   ├── lg_builder.py    #   图组装（纯连接）
│   │   │   ├── lg_nodes.py      #   节点函数（Router/Guardrails/RetrievalPlan/4 执行器）
│   │   │   ├── lg_react.py      #   ReAct 子图（构建 + 答案充分性检查）
│   │   │   ├── lg_models.py     #   LLM 模型 + 结构化输出模型
│   │   │   ├── lg_retrievers.py #   Retriever 接口 + 注册表 + 单例管理
│   │   │   ├── lg_states.py     #   AgentState 状态定义
│   │   │   ├── lg_context.py    #   记忆中间件 + 上下文构建
│   │   │   ├── lg_prompts.py    #   Prompt 加载器（YAML + 降级）
│   │   │   └── lg_prompts.yaml  #   Prompt 模板（热更新）
│   │   ├── memory/             # Redis STM / Milvus LTM / MemoryMiddleware
│   │   ├── models/             # SQLAlchemy 模型
│   │   ├── services/           # LLM 服务 / 会话 / 搜索 / 工厂
│   │   └── security/           # XML 隔离 + Prompt 注入防御
│   └── static/dist/            # 前端
├── rag_doc_parser/             # RAG 文档解析（PDF/DOCX → Milvus）
├── docker-compose.yml
└── requirements.txt
```

## 相关文档

- [CHANGELOG.md](CHANGELOG.md) — 版本更新日志
- [rag_doc_parser/README.md](rag_doc_parser/README.md) — RAG 文档解析模块
- [智能客服Agent项目详细文档.md](../智能客服Agent项目详细文档.md) — 完整架构文档

## License

MIT
