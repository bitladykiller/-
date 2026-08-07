# 配置参数与数据字段摘要（可提交副本）

完整版见仓库内 [docs/modules/07-配置参数与数据字段全览.md](../docs/modules/07-配置参数与数据字段全览.md)。
本文件保留为可提交摘要，方便从仓库根目录快速确认存储和事件语义。

> 状态：已实现并落地（v3.37）· 历史设计存档。正文若有与当前代码不一致处，以 `docs/modules/` 现行文档为准。

## 配置分层

| 类型 | 入口 | 读 `.env`？ |
|------|------|-------------|
| 连接/密钥 | `settings`（`config_models`） | 是 |
| STM/LTM/ReAct/上传/任务/RAG 改写 | `settings.app_config`（`app_config.py`） | **否**（代码默认） |

## 记忆关键默认值

| 项 | 默认 |
|----|------|
| STM 前缀 | `agent:stm` |
| STM TTL | 86400s |
| STM 窗口 | max_messages=16 |
| 压缩 | 6 轮 或 20 条；保留 4 轮 |
| LTM collection | `customer_agent_long_memory` |
| LTM search | top_k=5, score≥0.72 |
| LTM 去重 | top_k=3, sim≥0.88 |
| LTM 硬清理 | 每 1h；软删保留 7 天 |
| 画像缓存 | 1800s |
| 任务 key | `tenant:{tenant_id}:task:doc_parse:{task_id}`（v3.37 起含租户 namespace；tenant 为空回退旧格式 `task:doc_parse:{id}`）TTL 24h |

## 事件与后台执行

- 默认执行通道是 Redis Streams：`agent:events` / 消费组 `core`，承载
  `turn_completed` 和 `document_index_requested`；`EVENTS_INLINE_CONSUMER=1` 时由
  app 进程内嵌消费，设为 `0` 时用 `python -m app.worker` 独立消费。
- Streams 只提供**至少一次投递**：消息在 ACK 前崩溃会留在 PEL，随后由
  `XAUTOCLAIM` 重放；超过 3 次投递的失败消息进入死信流。
- MySQL `processed_events` 是消费 Inbox，按 `(tenant_id, event_type, event_id)`（v3.37 起
  含租户维度；此前为 `(event_type, event_id)`）认领并记录租约/状态/错误。业务
  handler 成功后先标记 `completed`，再 `XACK`。
- `background_tasks` 只保留 Redis 任务状态协议和事件基础设施不可用时的进程内回退，
  不是分布式队列。

## Collection 对照

| 用途 | Collection |
|------|------------|
| 长期记忆 | `customer_agent_long_memory`（env `MILVUS_COLLECTION_NAME`） |
| 文档 RAG | `rag_documents`（`RetrievalConfig`） |

## MySQL 核心表

- `users` / `conversations` / `messages`：`messages` 保存给用户查看和审计的完整历史；
  `messages.turn_event_id` 配合 `(conversation_id, turn_event_id, sender)` 唯一键防止
  `turn_completed` 重放时重复追加。
- `user_profiles` / `user_facts`
- `user_documents`（doc_id ↔ 文件名/version/hash）
- `processed_events`：Redis Stream 消费 Inbox（事件类型、稳定事件 ID、payload hash、
  处理租约、状态、失败/死信审计）

已有 MySQL 数据库升级时须运行
`configs/mysql-init/migration_stream_idempotency.sql`；Compose 的 `init.sql` 仅在新建
数据卷时自动执行。

## Redis STM keys

```text
agent:stm:{tenant}:{user}:{session}:messages|summary|meta|lock|turns|turn_lock
agent:events                         # Redis Stream；group=core，含 PEL / :dead
tenant:{tenant_id}:task:doc_parse:{task_id}   # v3.37 起任务状态 key；仅任务状态，不是待执行队列
```

## 详见

- [docs/modules/07-配置参数与数据字段全览.md](../docs/modules/07-配置参数与数据字段全览.md)
  — 全字段表、调参建议、源码索引
- [docs/superpowers/specs/2026-07-28-redis-stream-idempotency-design.md](../docs/superpowers/specs/2026-07-28-redis-stream-idempotency-design.md)
  — Inbox 幂等设计、失败重放与迁移语义
