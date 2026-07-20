# App 目录说明

`app/` 是当前项目唯一的主代码树。

## 业务域统一骨架

每个业务域（`chat` / `knowledge` / `user`）结构一致：

```text
app/<domain>/
  domain/              # 契约与纯规则
  application/         # 用例门面
  infrastructure/      # 技术实现
```

## 主目录

| 路径 | 角色 |
|------|------|
| `api/` | FastAPI 路由；只调 application 门面 |
| `chat/` | 对话 / Agent / KG / 会话 |
| `knowledge/` | 记忆 / 文档解析与索引 |
| `user/` | 用户身份与 durable 画像 |
| `shared/` | **唯一**全局共享内核（config/db/logger/security/task_queue） |
| `platform/` | 应用容器 / 生命周期装配 |
| `scripts/` | Compose 启动链路内部脚本 |

## 使用约定

- 新代码优先从领域包导入，不引入根级散包
- 依赖方向：`api → application → domain`；`infrastructure → domain`
- `user` 不依赖 `knowledge` / `chat`
- 业务域禁止再命名 `shared`（域内工具用 `infrastructure/utils`）
- 启动统一由 `docker compose` 完成
- 类型检查：mypy / basedpyright 以 **`app/`** 为准；`tests/` 用 pytest 验收（见 `pyrightconfig.json`）
