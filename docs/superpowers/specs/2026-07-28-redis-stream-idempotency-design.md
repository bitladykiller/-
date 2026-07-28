# Redis Stream 幂等消费设计

## 背景

当前 Redis Streams 消费组提供“至少一次投递”：消费者在处理成功但尚未 `XACK` 前崩溃时，消息会留在 PEL（pending entries list）并被 `XAUTOCLAIM` 重放。没有幂等控制时，`turn_completed` 会重复写历史/短期记忆/长期记忆，`document_index_requested` 会重复执行索引任务。

目标是在不改变 Redis Streams 至少一次投递模型的前提下，让业务副作用收敛为“同一事件至多生效一次”。

## 方案

采用 MySQL Inbox（`processed_events`）作为消费端持久化收件箱，并让核心业务落点使用同一事件 ID 防重。

### Inbox 数据模型

- 唯一键：`(event_type, event_id)`。
- 记录字段：事件类型、事件 ID、源 stream、源 entry ID、payload hash、状态、尝试次数、租约归属、租约截止时间、最近错误、完成时间、死信时间。
- 状态：`processing`、`completed`、`failed`。
- 同一 `event_id` 携带不同 payload hash 时视为生产端错误，不执行业务副作用。

### 消费状态机

1. 消费者解码 Stream entry 后先认领 Inbox 记录。
2. 已完成事件直接 `XACK`。
3. 租约未过期的处理中事件不并发执行，保留在 PEL。
4. 失败或租约过期事件可被重新认领。
5. 业务成功后先标记 `completed`，再 `XACK`。
6. 业务失败时记录错误并保持 PEL 重试；超过重试上限后进入死信流并在 Inbox 标记失败终态。

### 事件 ID

- `document_index_requested`：复用现有 `task_id`。
- `turn_completed`：发布前生成 `turn_id`，并同步作为 `event_id`。
- 兼容旧 PEL：缺少显式事件 ID 时，以 `stream + entry_id` 派生临时 ID。

### 业务落点

- MySQL `messages`：新增 `turn_event_id`，以 `(conversation_id, turn_event_id, sender)` 唯一键避免历史消息重复追加。
- Redis STM：事件链路传入 `turn_id`，会话级处理集合避免重放时重复追加本轮消息和重复增加轮次。
- 文档索引：`task_id` 写入 `file_info`，解析出的 chunk ID 可按任务 ID 规范化；`user_documents.last_task_id` 不匹配时旧任务结果不覆盖新任务。
- 用户画像：沿用 MySQL upsert 语义；重复执行时字段级覆盖保持收敛。

## 故障语义

- Inbox 不可用：不执行业务副作用，也不 ACK，等待 Stream 重试。
- 业务成功但 Inbox 完成标记失败：不 ACK，后续重放；业务落点根据事件 ID 收敛。
- 事件 ID 复用但 payload 不一致：不执行业务，最终进入死信，便于按事件 ID 排查。

## 测试范围

- 同一事件重放只执行一次 handler。
- 已完成事件重放会直接 ACK。
- payload hash 冲突不会执行业务。
- `turn_completed` 重放不会重复写 MySQL 历史。
- STM 在有 `turn_id` 时不重复追加。
- 文档索引任务把 `task_id/event_id` 透传到索引层，并跳过旧任务结果覆盖。

## 文档同步

README、系统总览、对话主图、知识检索模块、配置字段全览与 CHANGELOG 需要明确：Redis Streams 仍是至少一次投递；业务幂等由 MySQL Inbox 与落点事件 ID 共同保证。
