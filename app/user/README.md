# user 域

业务域标准骨架样板。

## 目录

```text
app/user/
  domain/                 # 画像契约、payload 规则
  application/            # 用户画像用例门面
  infrastructure/
    models/               # ORM
    repository/           # MySQL 持久化
```

## 边界

- **负责**：durable 用户画像读写、用户相关 ORM
- **不负责**：STM/LTM、文档索引、Agent 图

## 依赖

- 可依赖 `app.shared`（db/config/logger）
- **不**依赖 `app.knowledge` / `app.chat`
