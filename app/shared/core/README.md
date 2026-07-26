# Core 模块说明

`app/shared/core/` 放的是后端运行时最基础的公共设施。这里不处理具体业务，也不关心 Agent 节点细节，目标是把“配置、连接、日志”这些跨模块共用能力收敛到稳定入口。

## 结构分工

- `config.py`
  - 负责读取环境变量并组合成统一 `settings` 对象。
  - 按“基础设施配置 / 业务配置”拆分，减少单个 settings 类过度膨胀。
  - 当前主文件同时承接运行时字段解析，以及数据库 / Redis / Milvus URL 构造逻辑。
- `database.py`
  - 负责创建 SQLAlchemy 异步引擎、会话工厂和声明式 `Base`。
  - 当前统一使用 SQLAlchemy 2 的声明式基类，模型层通过类型注解直接表达字段和关系。
  - 当前主文件直接承接 SQLAlchemy 日志级别、异步引擎参数和会话工厂初始化样板。
  - 不承载具体表查询，也不表达业务事务规则。
- `logger.py`
  - 负责全局日志初始化入口和幂等状态。
  - 业务模块只应消费 `get_logger()` / `format_log_context()`，不要各自重复拼接日志样板。
  - 当前日志格式、root logger handler 策略和上下文字段拼装已经收口到主模块内部。
- `async_bridge.py`
  - 把第三方**同步 SDK**（pymilvus、LangChain Embeddings）的阻塞调用挪出事件循环。
  - 唯一入口 `run_blocking(func, *args, **kwargs)`，内部走 `asyncio.to_thread`。
  - **凡是在 `async def` 里调用同步客户端，一律经过这里**：直接调会卡住整个进程的
    所有并发请求（协程不让出控制权），而不只是当前这一个请求。
- `degradation.py`
  - 统一降级日志约定：区分「外部依赖抖动」和「我们自己写错了」。
  - 外部故障（Redis/超时/连接/OSError）记 `warning` 不打堆栈；
    其余一律 `logger.exception` 带完整堆栈，让代码缺陷可发现、可告警。
  - 替代散落各处的 `except Exception: return []` —— 那个模式曾让
    LTM 整条链路静默失效很久。
- `embeddings.py`
  - 全应用 **唯一** 的 embedding 构造入口，依 `settings` 选择 Ollama / HuggingFace。
  - 进程内共享实例：LTM 与 RAG **必须**用同一个模型，否则向量落在不同语义空间；
    HuggingFace 路径的模型权重也没必要在内存里存两份。

## 当前边界

- `core/` 只提供“基础能力”，不直接操作会话、上传、记忆或 LangGraph 流程。
- `core/` 可以被所有上层模块依赖，但反过来不应该依赖 `api/`、`chat/`、`knowledge/`、`user/` 的业务实现。
- 如果某段逻辑依赖具体业务字段或流程判断，它通常就不应该留在 `core/`。

## 后续维护建议

- 新增公共配置时，先判断它属于“基础设施参数”还是“业务行为参数”，避免所有字段继续堆进一个类。
- 新增数据库 helper 时，只保留连接层能力；实际查询和事务编排继续下沉到贴近业务的应用层或基础设施层模块。
- 如果日志 helper 开始依赖具体接口字段名或业务分支，说明它已经越过基础设施边界，应移回上层模块。
