# App 模块总览

`llm_backend/app/` 是后端主要代码目录。它的目标不是把所有逻辑堆在一个层级，而是把“协议入口、基础设施、业务流程、Agent 编排、记忆系统、持久化结构、安全工具”拆成边界相对清楚的子模块。

## 模块分工

- `api/`
  - FastAPI 路由入口。
  - 负责 HTTP 参数接收、响应转换和协议层错误映射。
- `core/`
  - 基础设施层。
  - 负责配置、数据库连接、日志等公共运行时能力。
- `services/`
  - 复用型业务服务层。
  - 负责会话、索引、任务队列、用户画像等可独立调用的业务流程。
- `lg_agent/`
  - LangGraph Agent 编排层。
  - 负责主图、子图、节点、状态和检索适配。
  - 当前正在按能力边界收敛到 `graph / retrieval / react / memory_bridge / modeling` 五类入口。
- `memory/`
  - 记忆系统层。
  - 负责短期记忆、长期记忆、用户画像标准化和记忆中间件。
  - 当前正在按能力边界收敛到 `config / stm / ltm / profile / orchestration` 五类入口。
- `models/`
  - MySQL 持久化模型层。
  - 只定义当前仍需要落库的结构化实体。
- `security/`
  - Prompt 防护工具层。
  - 负责最轻量的输入包裹与转义能力。

## 推荐阅读顺序

1. 先看 `api/README.md`，理解请求从哪里进入。
2. 再看 `services/README.md` 和 `lg_agent/README.md`，理解主业务流程与 Agent 编排。
3. 然后看 `memory/README.md`、`models/README.md`、`core/README.md`，补齐运行时支撑层。
4. 最后看 `security/README.md`，理解 Prompt 输入保护放在哪一层。

## 当前架构取向

- 协议层尽量薄：HTTP 细节停留在 `api/`
- 业务流程可复用：复用逻辑尽量收敛到 `services/`
- Agent 编排单独隔离：LangGraph 相关主流程不与普通业务服务混写
- 记忆和持久化分离：记忆系统与 MySQL 模型各自维护自己的边界
