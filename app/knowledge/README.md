# knowledge 域

记忆与文档索引域。骨架与 `user` / `chat` 对齐。

## 目录

```text
app/knowledge/
  domain/                 # 记忆 schemas、prompt 组装规则
  application/            # IndexingService / DocumentService / document_indexing_job
  infrastructure/
    models/               # UserDocument（MySQL user_documents）
    repository/           # UserDocumentRepository
    stm/                  # Redis 短期记忆
    ltm/                  # Milvus 长期记忆（软删 + hard_purge）
    orchestration/        # 记忆抽取与中间件
    doc_parser/           # Markdown/PDF/DOCX 解析与 RAG 写入（策略 2 软删/version）
```

## 边界

- **负责**：STM / LTM / 记忆编排 / 文档解析与索引 / 用户文档元数据
- **不负责**：HTTP 路由（在 `app/api`）、会话元信息 CRUD、Agent 路由决策

## 持久化说明

| 存储 | 内容 |
|------|------|
| Redis | STM、上传 task 状态 |
| Milvus | LTM 记忆；RAG `rag_documents` chunks（`doc_id`/`version`/`is_deleted`） |
| MySQL `user_documents` | 稳定 `doc_id`、title、hash、version、status（列表/更新绑定） |
| Redis Streams + MySQL `processed_events` | `document_index_requested` 的至少一次投递与消费 Inbox；`task_id` 是索引事件的稳定 ID |
| 本地文件 | `uploads/` 落盘原文 |

索引消费者会把 `task_id` 透传为 `event_id`：chunk ID 固定为事件派生值，Milvus
使用 upsert / 已写版本检查，因而在 XACK 前重放不会再插入一份文档。

迁移：已有环境除 `configs/mysql-init/migration_user_documents.sql` 外，还需执行
`configs/mysql-init/migration_stream_idempotency.sql`；新数据卷由 `init.sql` 建表。

## 依赖

- 可依赖 `app.shared`、`app.user.domain`（画像契约）
- 被 `chat` 图节点通过编排层消费
