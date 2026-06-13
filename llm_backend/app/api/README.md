# API 模块说明

`llm_backend/app/api/` 负责暴露 FastAPI 路由入口。这里的目标不是承载业务细节，而是把“HTTP 协议转换”和“服务调用编排”控制在薄接口层内。

## 结构分工

- `__init__.py`
  - 统一注册路由。
  - 负责把不同接口模块挂载到主 `APIRouter`，避免在入口文件里散落导入。
- `common.py`
  - 收口 API 薄层共享约定，例如简单消息响应和统一的 500 包装。
  - 目标是减少路由文件里的重复样板，不承载业务逻辑。
- `langgraph.py`
  - 提供 Agent 主查询入口。
  - 只负责接收表单参数、构造 `thread_config`、返回 SSE 流。
  - 不直接处理 LangGraph 内部节点、记忆注入或检索逻辑。
- `langgraph_support.py`
  - 负责 LangGraph API 共享的 thread config / input state 构造、chunk 过滤与 SSE 响应包装。
  - 这样 `langgraph.py` 主文件更聚焦“组装 graph 调用并返回流式响应”。
- `upload.py`
  - 处理文档上传、基础校验和后台任务提交。
  - 只负责上传入口，不承担文档切分、向量化和索引写入细节。
  - 上传可接受的文件类型以 `services/document_formats.py` 为准，避免 API 层和索引层各维护一份扩展名名单。
- `upload_support.py`
  - 负责上传接口共享的响应契约、基础校验和受理响应构造。
  - 这样 `upload.py` 主文件更专注于“提交后台任务”和“返回 HTTP 响应”。
- `upload_storage_support.py`
  - 负责上传文件的内容校验、落盘目标构造、文件元信息组装和最终落盘。
  - 这样 `upload_support.py` 不再同时背负 HTTP 文案和磁盘落盘细节。
- `conversations.py`
  - 处理会话列表、创建、删除、重命名。
  - 只做请求参数解析和服务层调用，不直接碰数据库。

## 当前边界

- API 层只做参数接收、响应组装和 HTTP 状态码转换。
- 业务规则放在 `services/`，不要把数据库访问或索引流程塞回路由函数。
- LangGraph 运行细节放在 `lg_agent/`，上传解析细节放在 `indexing_service.py` 和后台任务层。

## 后续维护建议

- 新增接口时，先判断它属于“会话管理 / Agent 查询 / 上传索引”中的哪一类，再决定放进哪个模块。
- 如果某个路由函数开始出现大量路径构造、数据库访问或复杂条件分支，应优先把逻辑下沉到 `services/`。
- 如果某个路由文件开始同时堆放“接口编排 + 大量上传/路径/响应 helper”，优先拆出 `*_support.py`，保持主文件薄一些。
- 如果某个流式接口文件开始同时堆放“graph 调用 + SSE chunk 过滤 + thread config 构造”，优先把流式包装 helper 抽到 `*_support.py`。
- 如果新增的是一整类接口，而不是某个接口的一个小变体，优先新建独立路由文件，而不是继续堆在现有模块里。
