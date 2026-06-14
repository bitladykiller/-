# Core 模块说明

`app/shared/core/` 放的是后端运行时最基础的公共设施。这里不处理具体业务，也不关心 Agent 节点细节，目标是把“配置、连接、日志”这些跨模块共用能力收敛到稳定入口。

## 结构分工

- `__init__.py`
  - 负责暴露 `settings`、数据库会话工厂、`Base`、日志 helper 等常用基础设施对象。
  - 这样上层模块如果只需要消费稳定入口，可以优先从 `app.shared.core` 导入，而不是自行判断子模块边界。
- `config.py`
  - 负责读取环境变量并组合成统一 `settings` 对象。
  - 按“基础设施配置 / 业务配置”拆分，减少单个 settings 类过度膨胀。
  - 当前主文件聚焦稳定导出；运行时字段解析和数据库 / Redis / Milvus URL 构造已经收口到 `config_runtime.py`。
- `config_runtime.py`
  - 负责组合基础设施与业务配置。
  - 负责多子配置对象之间的字段解析与数据库 / Redis / Milvus 连接地址拼装。
- `database.py`
  - 负责创建 SQLAlchemy 异步引擎、会话工厂和声明式 `Base`。
  - 当前统一使用 SQLAlchemy 2 的声明式基类，模型层通过类型注解直接表达字段和关系。
  - 当前主文件只保留 engine / session / Base 出口；引擎参数和日志样板已下沉到 `database_support.py`。
  - 不承载具体表查询，也不表达业务事务规则。
- `database_support.py`
  - 负责 `database.py` 共享的纯 helper。
  - 当前承接 SQLAlchemy 日志级别配置、异步引擎参数构造和会话工厂样板，便于单测和复用。
- `logger.py`
  - 负责全局日志初始化入口和幂等状态。
  - 业务模块只应消费 `get_logger()` / `format_log_context()`，不要各自重复拼接日志样板。
  - 当前日志格式、root logger handler 策略和上下文字段拼装已经收口到主模块内部。

## 当前边界

- `core/` 只提供“基础能力”，不直接操作会话、上传、记忆或 LangGraph 流程。
- `core/` 可以被所有上层模块依赖，但反过来不应该依赖 `api/`、`chat/`、`knowledge/`、`user/` 的业务实现。
- 如果某段逻辑依赖具体业务字段或流程判断，它通常就不应该留在 `core/`。

## 后续维护建议

- 新增公共配置时，先判断它属于“基础设施参数”还是“业务行为参数”，避免所有字段继续堆进一个类。
- 新增数据库 helper 时，只保留连接层能力；实际查询和事务编排继续下沉到贴近业务的应用层或基础设施层模块。
- 如果日志 helper 开始依赖具体接口字段名或业务分支，说明它已经越过基础设施边界，应移回上层模块。
