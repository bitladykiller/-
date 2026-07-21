# 配置参数与数据字段全览（可提交副本）

完整版维护在本机：`docs/配置参数与数据字段全览.md`（`docs/` 默认 gitignore）。

本文件为 **可入库摘要 + 指针**，避免仓库无入口。

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
| 任务 key | `task:doc_parse:{id}` TTL 24h |

## Collection 对照

| 用途 | Collection |
|------|------------|
| 长期记忆 | `customer_agent_long_memory`（env `MILVUS_COLLECTION_NAME`） |
| 文档 RAG | `rag_documents`（`RetrievalConfig`） |

## MySQL 核心表

- `users` / `conversations`（消息在 Redis）
- `user_profiles` / `user_facts`
- `user_documents`（doc_id ↔ 文件名/version/hash）

## Redis STM keys

```text
agent:stm:{tenant}:{user}:{session}:messages|summary|meta|lock
```

## 详见

打开仓库内（若已生成本机 docs）：

- `docs/配置参数与数据字段全览.md` — 全字段表、调参建议、源码索引
