# chat 域

对话与 Agent 域。骨架与 `user` / `knowledge` 对齐。

## 目录

```text
app/chat/
  domain/                 # 领域契约边界说明（最小）
  application/            # 会话服务、Agent 问答门面
  infrastructure/
    utils/                # 域内小工具（禁止命名 shared）
    models/               # 会话 ORM
    repository/           # 会话持久化
    graph/                # LangGraph 主图与节点
    kg/                   # Neo4j / Text2Cypher
    react/                # ReAct 子图
    retrievers/           # 检索器抽象与实现
    modeling/             # LLM 代理与结构化输出
```

## 边界

- **负责**：会话元信息、Agent 图执行、KG/RAG 检索编排入口
- **不负责**：全局配置、事件/任务基础设施、用户画像持久化

`graph/lifecycle_nodes.after_response` 只生成稳定的 `turn_id` 并发布
`turn_completed`；Redis Streams 消费者随后写 MySQL 历史和记忆链。重复投递时，
Inbox、`messages.turn_event_id` 唯一键与 STM 回合标记共同避免重复写入。

## 命名

- 全局共享只允许 `app.shared`
- 域内工具在 `infrastructure/utils`，**不要**再建 `shared`

## 依赖

- 可依赖 `app.shared`、`app.knowledge`（记忆/检索）、`app.user`（画像）
