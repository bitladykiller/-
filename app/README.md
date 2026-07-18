# App 目录说明

`app/` 是当前项目唯一的主代码树。

## 主目录

- `api/` — FastAPI 路由；只调用 application 门面
- `chat/` — 对话域（会话、Agent 图、检索器、KG）
- `knowledge/` — 记忆、文档解析与索引
- `user/` — 用户身份与 durable 画像
- `shared/` — 配置、数据库、日志、安全、任务队列、共享检索
- `platform/` — 应用容器 / 生命周期装配
- `scripts/` — Compose 启动链路内部脚本

## 使用约定

- 新代码优先从领域包导入，不引入根级散包
- 依赖方向：`api → application → infrastructure/domain`；`user` 不依赖 `knowledge`
- 启动统一由 `docker compose` 完成
