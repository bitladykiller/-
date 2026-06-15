# App 目录说明

`app/` 是当前项目唯一的主代码树。日常开发默认只需要在这里定位代码，不需要再区分任何旧目录或兼容入口。

## 主目录

- `api/`
  - FastAPI 路由、请求转换和 SSE 输出封装。
- `chat/`
  - 对话域，包含 LangGraph 主图、检索器、ReAct 子图和会话编排。
- `knowledge/`
  - 记忆、向量检索、用户画像和相关编排逻辑。
- `user/`
  - 用户与会话的持久化模型、相关应用服务。
- `shared/`
  - 配置、数据库、日志、安全等跨领域共享基础设施。
- `scripts/`
  - 建表、重置表等应用内维护脚本。

## 使用约定

- 新代码优先从 `app.chat`、`app.knowledge`、`app.user`、`app.shared` 导入。
- 应用启动统一由 `docker compose` 调用 `configs/docker/app/start.sh` 完成。
