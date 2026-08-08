# 08 - SaaS 多租户架构

> 本篇回答一个问题：**如何把一个"单租户"的智能客服 Agent 升级成真正 SaaS 级多租户？**
> 事实口径：以当前仓库 `app/`、`configs/mysql-init/`、`scripts/`、`tests/` 为准。

## 0. 核心原则（一句话版）

> **身份上确认 tenant，执行中传播 tenant，存储时写 tenant，查询时强制 tenant，异步时携带 tenant，运维时也按 tenant 治理。**

多租户不能只靠"所有表加一个 `tenant_id`"——tenant 必须是**经过鉴权确认的一级安全边界**，
并且从 HTTP → Agent → 异步事件 → MySQL → Redis → Milvus → Neo4j 全链路不可丢失。

## 1. 全链路 tenant 流动图

```text
                    ┌──────────────┐
                    │     JWT      │
                    │ user_id      │
                    │ tenant_id    │
                    │ roles/scopes │
                    └──────┬───────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │  TenantContext  │  deps.get_current_user
                  │ tenant_id       │  └─ JWT 验签
                  │ user_id         │  └─ tenant_memberships 校验
                  │ role            │  └─ 写入 contextvars
                  └────────┬────────┘
                           │
        ┌──────────────────┼──────────────────────┐
        ▼                  ▼                      ▼
      MySQL              Redis                  Milvus
 tenant_id 列        tenant namespace        tenant filter
        │                  │                      │
        ▼                  ▼                      ▼
 Conversation            STM / 画像 /         RAG / LTM
 Document / Profile      任务 / 限流
        │
        └───────────────┬─────────────────────────
                        ▼
                     Events
                tenant_id 固化进事件 payload
                worker 消费后恢复 TenantContext
```

## 2. 身份模型（Phase 1）

### 2.1 数据模型：users ↔ tenants 多对多

**不用 `users.tenant_id`**，因为那只支持"一个用户只属于一个租户"。
用 memberships 支持跨组织账号：

```text
users
  │
  └── tenant_memberships          UNIQUE(tenant_id, user_id)
           │
           └── tenants
```

| 表 | 关键字段 | 说明 |
|---|---|---|
| `tenants` | `id` VARCHAR(64) PK（`default` / `t_xxx`）、`name`、`status`、`plan` | 租户主体 |
| `tenant_memberships` | `tenant_id`、`user_id`、`role`(owner/admin/member/viewer)、`status` | 用户-租户归属 |

注册即创建"个人空间"租户（用户成为 owner）；登录时把用户**最早加入的有效租户**写进 JWT。

### 2.2 JWT 与 AuthenticatedUser

- `issue_access_token(user_id, username, tenant_id, ...)`：payload 增加 `tenant_id`。
- `AuthenticatedUser` 增加 `tenant_id: str`（默认 `"default"` 兼容旧令牌）。
- `verify_access_token` 从载荷还原 `tenant_id`——**但令牌声明不可信**，见下节。

### 2.3 请求鉴权：membership 是最终裁判

`app/api/deps.py` 的 `get_current_user` 现在是一个完整链：

```text
Authorization: Bearer JWT
        ↓ verify_access_token
AuthenticatedUser{user_id, tenant_id}
        ↓ TenantService.validate_membership(user_id, tenant_id)
user ∈ tenant AND membership.status=active AND tenant.status=active
        ↓ set_tenant_context(...)
TenantContext{tenant_id, user_id, role} → contextvars
```

角色（role）只来自 membership 查询结果，不来自令牌。校验失败统一 401。

### 2.4 TenantContext 与 contextvars

`app/shared/core/identity.py` 提供：

```python
@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: int
    role: str = ""
```

同步请求链任意一层都可以读取同一个可信上下文
（API → Service → Repository → Retriever → Event Publisher），
**不再信任请求参数**（查询参数里出现 `tenant_id` 一律视为客户端攻击面）。

### 2.5 新增 API

| 端点 | 说明 |
|---|---|
| `POST /api/auth/register` | 注册即登录，自动创建个人租户（响应含 `tenant_id`） |
| `POST /api/auth/login` | 登录，JWT 携带活跃租户（响应含 `tenant_id`） |
| `GET /api/auth/me` | 返回 `{user_id, username, tenant_id, role}` |
| `GET /api/auth/tenants` | 列出用户加入的全部租户（角色/状态） |
| `POST /api/auth/switch-tenant` | 切换活跃租户，重新签发令牌 |

## 3. MySQL：所有业务表 tenant-scoped（Phase 2）

### 3.1 表结构升级

| 表 | 改造 | 唯一约束 |
|---|---|---|
| `conversations` | +`tenant_id`，索引 `(tenant_id, user_id)`、`(tenant_id, id)` | — |
| `messages` | **间接 tenant**（JOIN conversations 强制条件，不冗余列） | `(tenant_id, conversation_id, turn_event_id, sender)` 语义经 JOIN |
| `user_profiles` | 主键升级为 `(tenant_id, user_id)` | PK 即唯一 |
| `user_facts` | +`tenant_id` | `(tenant_id, user_id, active_fact_key)`、`(tenant_id, user_id, fact_key, source_turn_id)` |
| `user_documents` | +`tenant_id` | `(tenant_id, doc_id)`——doc_id 租户内唯一 |
| `turn_view_status` | +`tenant_id` | `(tenant_id, turn_id, view_name)` |
| `memory_hit_events` | +`tenant_id` | `(tenant_id, turn_id, memory_id)` |
| `processed_events` | +`tenant_id` | `(tenant_id, event_type, event_id)` |
| `compression_tasks` | 已有 `tenant_id`，默认值对齐 `default` | — |

**messages 为什么不做直接 tenant 化**：messages 通过 `conversation_id` FK 到
conversations，Repository 层（`MessageRepository`）的查询一律 `JOIN conversations`
强制 `tenant_id` 条件——即使调用方漏做了会话归属校验也不会跨租户读写。

### 3.2 迁移脚本

- 全新部署：`configs/mysql-init/init.sql`（已含全部新表与种子租户）
- 存量升级：`configs/mysql-init/migration_saas_tenancy.sql`（加列 → 重建唯一键 → 种子 default 租户与 memberships）

## 4. Repository 纪律（Phase 3）

**禁止无租户上下文的查询路径**，业务热点路径签名：

```python
# 会话
repo.get_owned(tenant_id, conversation_id, user_id)      # 组织边界 + 租户内归属
repo.list_by_user(tenant_id, user_id)
# 文档
repo.get_owned(tenant_id, user_id, doc_id)
repo.get_by_doc_id(tenant_id, doc_id)                    # worker 回写路径同样带租户
# 画像
repo.get_profile(db, tenant_id, user_id)
```

- `tenant_id` 是**组织边界**，`user_id` 是**组织内部资源归属边界**，二者组合才完整。
- `doc_id` 已升级为租户内唯一：两个租户可以各自拥有 `policy.pdf` 的 doc_id。
- worker 任务回写路径（`bind_task_id` / `apply_indexing_result`）也必须带租户，
  tenant_id 从事件 payload 恢复（见 §8）。

## 5. Redis：tenant namespace（Phase 4）

| Key 模板 | 说明 |
|---|---|
| `agent:stm:{tenant}:{user}:{session}:messages/summary/meta/...` | STM，改造前已达标，保留 |
| `tenant:{tenant_id}:user:profile:{user_id}` | 画像缓存（原 `user:profile:{user_id}`） |
| `tenant:{tenant_id}:task:doc_parse:{task_id}` | 任务状态（原 `task:doc_parse:{task_id}`；tenant 为空时兼容旧 key） |
| `ratelimit:sse:{tenant_id}` | **租户级** SSE 并发配额（`sse_max_concurrent_per_tenant`，默认 0=不启用） |
| `ratelimit:sse:{tenant_id}:user:{user_id}` | **用户级** SSE 并发配额 |

限流器（`SseConcurrencyLimiter`）现在"先占租户槽位、再占用户槽位"：
任一级超限即 429，用户级失败会回收已占用的租户槽位（计数一致）。

## 6. Milvus：三级可见性（Phase 5）

### 6.1 RAG chunk 新增字段

| 字段 | 语义 |
|---|---|
| `tenant_id` | 租户边界；`""` 表示平台公共（visibility=global） |
| `owner_id` | `global`=公共；`""`=组织共享；user_id=个人私有 |
| `visibility` | `global` \| `tenant` \| `private` |

### 6.2 检索过滤（双层）

```text
第 1 层（常开，SaaS 隔离底线）：
    (tenant_id == "当前租户") or (tenant_id == "")
        —— 本租户数据 + 平台公共数据

第 2 层（rag_visibility.enabled 开启时）：
    (visibility == "global")
    or (visibility == "tenant" and tenant_id == "当前租户")
    or (visibility == "private" and tenant_id == "当前租户" and owner_id == "当前用户")
        —— 用户可见知识 = 平台公共 + 本组织共享 + 本人私有
```

实现位置：`doc_lifecycle.tenant_boundary_filter` / `tenant_visibility_filter`，
`milvus_store._visibility_filter` 从 contextvars 取租户与用户。

### 6.3 上传可见域

`visibility` 参数扩展为 `global | tenant | private`：
`upload.resolve_chunk_visibility` 产出 chunk 的 `(owner_id, tenant_id, visibility)` 三元组；
**MySQL 行归属的 tenant_id 始终是请求方真实租户**（global 文档的空串租户只落在 Milvus chunk 上）。

### 6.4 LTM（长期记忆）

改造前已 tenant-aware：collection schema 带 `tenant_id`，检索 filter
`tenant_id == "{t}" AND user_id == "{u}" AND is_deleted == false`。
**演进方向**（本阶段未实现，见 §10）：`scope_type = user | tenant`，
支持"组织共享记忆 + 个人长期记忆"同时召回。

### 6.5 存量数据迁移

Milvus collection 新增了 `tenant_id` / `visibility` 字段，存量 chunk 需要：
- 方式 A：重新上传文档（reindex 自动写入新字段）
- 方式 B：对存量集合执行 `UPDATE` 补标 `tenant_id = 'default'`、`visibility = 'global'`

## 7. Neo4j：执行层强制约束（Phase 6）

### 7.1 问题

LLM 生成 Cypher 时，**tenant 约束属于 validation / execution policy，
不能交给 prompt 让 LLM"自觉"**——某一次漏写 WHERE 就是跨租户泄漏。

### 7.2 确定性注入

`app/chat/infrastructure/kg/tenant_cypher.py` 在唯一的执行闸口
（`text2cypher_workflow` 的两处 `graph.query`）做确定性改写：

```cypher
-- LLM / 模板产出：
MATCH (o:Order) WHERE o.orderId = $order_id RETURN o

-- 执行层注入后：
WITH $__tenant_id AS __tenant_boundary
MATCH (o:Order) WHERE o.tenant_id = __tenant_boundary AND o.orderId = $order_id
RETURN o
```

要点：
- 提取每个 MATCH 子句中**全部带变量的节点**（`(v:Label)` / `(v)`），逐个补条件；
- 已存在 WHERE 时插到 WHERE 开头，否则在首个 RETURN 前插入，没有 RETURN 则追加；
- 租户 ID 走参数绑定（`$__tenant_id`），绝不拼字面量；
- 提取不到节点变量（如 `RETURN 1` 健康检查）时原样返回并告警。

### 7.3 数据前提

所有业务节点必须带 `tenant_id` 属性：
- 全新导入：`scripts/neo4j-import.sh` 已内置"打标 + 索引"步骤；
- 存量图：`MATCH (n) WHERE NOT exists(n.tenant_id) SET n.tenant_id = 'default'`。

## 8. 异步事件：tenant_id 固化进 payload（Phase 7）

### 8.1 为什么必须显式携带

```text
HTTP 请求结束 → ContextVar 已失效 → 异步 worker 在另一台机器执行
```

ContextVar 无法跨进程传播，所以：

```text
同步链：TenantContext（contextvars）
跨进程：event.tenant_id（payload 固化）
恢复后：set_current_tenant_id(event.tenant_id)
```

### 8.2 事件协议

`turn_completed` 与 `document_index_requested` 均携带顶层 `tenant_id`。
`streams.py` 消费端在调用业务 handler 前恢复 contextvars；业务 handler
（`events.py`）再恢复一次做双保险，并把它传给 `run_task_with_status_updates`
（任务状态 key 的租户 namespace）。

### 8.3 幂等键 tenant-scoped

`processed_events` 唯一键从 `(event_type, event_id)` 升级为
`(tenant_id, event_type, event_id)`；`EventInbox` 全部方法显式接收 `tenant_id`。
价值不是防 UUID 碰撞，而是**租户级审计 / 查询 / 清理 / 死信处理**都可以直接
`WHERE tenant_id = ?`。

## 9. 文件存储

落盘目录按租户隔离（`app/api/upload.py`）：

```text
uploads/{tenant_id}/{user_uuid}/{timestamp}/xxx.pdf
```

对象存储演进方向：`s3://bucket/tenants/{tenant_id}/documents/{doc_id}`，
Enterprise 可一租户一 bucket；presigned URL 生成前必须校验租户归属。

## 10. 已实现 vs 演进方向（Phase 8+）

| 能力 | 状态 | 说明 |
|---|---|---|
| tenants + memberships | ✅ | 注册建个人租户、登录携带活跃租户、切换租户 |
| MySQL 全表 tenant 化 | ✅ | 含唯一键重定义与迁移脚本 |
| Repository 强制 tenant | ✅ | 无租户上下文的查询路径已消灭 |
| Redis namespace | ✅ | 画像 / 任务 / 双层限流 |
| Milvus 三级可见性 | ✅ | global / tenant / private |
| Neo4j 执行层约束 | ✅ | 确定性注入，不依赖 LLM |
| 事件携带 tenant + 幂等键 tenant-scoped | ✅ | Inbox 按租户运维 |
| 文件存储租户目录 | ✅ | uploads/{tenant}/{user} |
| LTM 组织级记忆（scope_type=tenant） | ⏳ 演进 | 检索改为 `tenant AND (scope_type=tenant OR (scope_type=user AND user=me))` |
| 一租户一 Collection / 独立实例 | ⏳ 演进 | hybrid isolation：普通租户共享集合，Enterprise 独立 collection，监管客户独立实例 |
| RBAC 权限系统 | ⏳ 演进 | 角色已有（owner/admin/member/viewer），权限点（document.write 等）待建 |
| 租户配额（quota） | ⏳ 演进 | `quota:{tenant}:llm_tokens` 等，Redis 已有双层限流底座 |
| 审计日志 / 用量计费 | ⏳ 演进 | 事件已带 tenant_id，可直接聚合 |
| 租户删除工作流 | ⏳ 演进 | 跨 MySQL/Redis/Milvus/Neo4j/文件的 saga + deletion_status |
| 按租户加密（DEK/KMS/BYOK） | ⏳ 演进 | Enterprise 规格 |

## 11. 隔离模型选择

当前实现是 **pooled + logical isolation**（共享基础设施 + 逻辑隔离），
也是绝大多数 SaaS 的默认选择：

| 客户 | MySQL | Redis | Milvus | Neo4j |
|---|---|---|---|---|
| Free/Pro（当前） | Shared DB + tenant_id | Shared + namespace | Shared Collection + filter | Shared + 执行层约束 |
| Enterprise（演进） | Shared DB/Schema | Shared namespace | Dedicated Collection | Dedicated DB |
| Regulated（演进） | Dedicated DB | Dedicated Redis | Dedicated Cluster | Dedicated instance |

## 12. 面试要点（浓缩版）

> SaaS 多租户不能只依赖数据库加 tenant_id，而应该建立端到端的 Tenant Context。
> 用户通过 JWT 完成身份认证，再通过 membership 确认其属于哪个 tenant，
> 之后生成不可篡改的 TenantContext。同步请求通过 context 传播，
> 异步任务则显式把 tenant_id 写进 event。MySQL 所有业务查询强制 tenant 条件，
> Redis 通过 tenant namespace 隔离，Milvus RAG/LTM 通过 tenant metadata filter 隔离，
> Neo4j 则在 deterministic execution layer 强制 tenant 约束，而不是让 LLM 决定是否添加。
> 权限再在 tenant boundary 内做 RBAC。普通客户使用 shared infrastructure +
> logical isolation，Enterprise 或监管客户再升级成 collection/database/cluster 级物理隔离。

本项目里对应的具体证据：

1. **JWT 声明 tenant**（`auth_service.issue_access_token`），**membership 校验**（`deps.get_current_user` → `TenantService.validate_membership`），**contextvars 承载**（`identity.TenantContext`）。
2. **MySQL 全表 tenant_id**：`migration_saas_tenancy.sql`；唯一键全部 tenant-scoped（doc_id、幂等键、版本化事实键）。
3. **Repository 强制 tenant**：`get_owned(tenant_id, conversation_id, user_id)`，不存在无租户查询；messages 用 JOIN 间接强制。
4. **Redis namespace**：`tenant:{tenant_id}:user:profile:{user_id}`、`tenant:{tenant_id}:task:doc_parse:{task_id}`、双层限流 `ratelimit:sse:{tenant}` + `:{tenant}:user:{user}`。
5. **Milvus 三级可见性**：`tenant_boundary_filter`（常开）+ `tenant_visibility_filter`（global/tenant/private 开关）。
6. **Neo4j 执行层注入**：`tenant_cypher.inject_tenant_constraint` 在 `graph.query` 闸口确定性改写，参数化绑定。
7. **事件携带 tenant**：payload 顶层 `tenant_id`，worker 恢复 contextvars；`processed_events` 幂等键升级 `(tenant_id, event_type, event_id)`。
