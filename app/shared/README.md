# app.shared — 唯一全局共享内核

## 职责

- `core/`：配置、数据库、日志、JSON 工具、异步桥接（`async_bridge`）、降级日志约定（`degradation`）、embedding 工厂（`embeddings`）
- `security/`：Prompt 防护
- `retrieval/`：Milvus 混合检索公共核
- `streams.py`：Redis Streams 发布、消费组、PEL 重放、死信；容器注入 Inbox 后，
  已完成事件只 ACK，不重复执行业务 handler
- `background_tasks.py`：进程内后台任务 + Redis 状态上报（非分布式队列）

## 规则

1. **全项目只有一个 shared**：`app.shared`
2. 业务域（chat/knowledge/user）内 **禁止** 再命名目录为 `shared`
3. 业务域跨节点小工具放在该域 `infrastructure/utils`
4. shared **不**依赖 chat/knowledge/user

`streams.py` 是持久化事件通道，`background_tasks.py` 只是事件基础设施不可用时的
进程内回退。新增跨进程任务应走 Streams，并提供稳定 `event_id`；业务幂等状态由
`app.platform.event_inbox`（而非 shared）持久化到 MySQL。
