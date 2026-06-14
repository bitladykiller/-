# 架构概览

## 1. 当前结构

项目当前只保留一套主代码结构：

```text
deepseek_agent/
├── app/
│   ├── api/
│   ├── chat/
│   ├── knowledge/
│   ├── user/
│   ├── shared/
│   └── scripts/
├── docs/
├── scripts/
├── tests/
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

旧的 `app.lg_agent`、`app.memory`、`app.services`、`llm_backend` 兼容目录已经移除。

## 2. 分层规则

### 2.1 `app/api`

- 负责 HTTP 路由、请求解析、SSE 输出
- 不负责业务编排和检索实现

### 2.2 `app/chat`

- 对话主流程
- LangGraph 主图
- 检索器、ReAct、KG 子图

### 2.3 `app/knowledge`

- 短期记忆
- 长期记忆
- 用户画像
- 记忆编排

### 2.4 `app/user`

- 用户与会话相关模型
- 用户画像相关应用服务

### 2.5 `app/shared`

- 配置
- 数据库
- 日志
- Prompt 安全工具

### 2.6 `app/scripts`

- 建表
- 重置表
- 启动辅助脚本

## 3. 依赖方向

默认依赖方向：

```text
api/interface
    -> application
    -> domain
infrastructure
    -> domain
shared
    -> 被上层消费
```

不再保留任何旧路径转发层。

## 4. 启动方式

### 本地开发

```bash
python -m app.run
```

### Docker Compose

```bash
docker compose up -d --build
```

容器启动前会执行 `python -m app.scripts.bootstrap_compose_db`。
