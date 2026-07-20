# knowledge 域

记忆与文档索引域。骨架与 `user` / `chat` 对齐。

## 目录

```text
app/knowledge/
  domain/                 # 记忆 schemas、prompt 组装规则
  application/            # 文档索引用例（IndexingService）
  infrastructure/
    stm/                  # Redis 短期记忆
    ltm/                  # Milvus 长期记忆
    orchestration/        # 记忆抽取与中间件
    doc_parser/           # Markdown/PDF/DOCX 解析与 RAG 写入
```

## 边界

- **负责**：STM / LTM / 记忆编排 / 文档解析与索引
- **不负责**：HTTP 路由、会话元信息 CRUD、Agent 路由决策

## 持久化说明

本域主存储是 Redis / Milvus / 文件，**不是** MySQL repository。  
因此不强制建立空的 `infrastructure/models` 或 `repository` 目录。

## 依赖

- 可依赖 `app.shared`、`app.user.domain`（画像契约）
- 被 `chat` 图节点通过编排层消费
