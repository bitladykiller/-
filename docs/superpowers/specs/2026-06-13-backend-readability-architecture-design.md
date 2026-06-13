# 后端可读性与架构优化设计

日期：2026-06-13

## 1. 背景

当前项目已经完成过一轮明显的代码清理和职责拆分，`api / services / core / main` 这些外层边界已经基本形成了“薄入口 + 业务编排 + 运行时装配”的结构。

但 `llm_backend/app/lg_agent` 和 `llm_backend/app/memory` 仍然是当前项目中最主要的复杂度中心，存在以下问题：

1. 复杂度仍然集中在少数主模块与大量 helper 文件之间。
2. 目录边界更多体现为 `*_support.py`、`*_runtime.py`、`*_utils.py` 这类技术拆分，而不是能力边界拆分。
3. 阅读主流程时，经常需要在 3 到 5 个文件之间来回跳转，认知成本较高。
4. 继续用“把大文件再拆成更多 helper 文件”的方式，已经难以继续显著提升可读性。

这次优化的目标不是重写系统，也不是引入过重的分层理论，而是在保留现有行为和已有测试价值的前提下，把当前结构调整为更容易理解、更容易维护的形态。

## 2. 设计目标

### 2.1 主要目标

1. 提升 `memory` 和 `lg_agent` 两个复杂子系统的可读性。
2. 让目录边界直接表达能力边界，而不是继续表达 helper 类型边界。
3. 保持当前外部行为、核心函数签名和主要调用语义尽量稳定。
4. 为后续继续演进检索链路、记忆链路和 Agent 编排提供更清晰的落点。

### 2.2 非目标

1. 不重写业务逻辑。
2. 不一次性引入新的重型架构层次，例如完整的 domain/application/infrastructure 四层重构。
3. 不在这一轮大动 `api / services / core / main`。
4. 不为了结构美观而改动已经清晰稳定的外层模块。

## 3. 设计范围

### 3.1 本轮重点范围

1. `llm_backend/app/memory`
2. `llm_backend/app/lg_agent`
3. 与上述目录直接相关的 README 与结构说明文档
4. 与迁移相关的 import 路径和兼容导出

### 3.2 本轮非重点范围

以下目录不作为本轮大规模重构目标，只允许做受影响的 import 对齐或最小配套修改：

1. `llm_backend/app/api`
2. `llm_backend/app/services`
3. `llm_backend/app/core`
4. `llm_backend/main.py`
5. `llm_backend/main_support.py`
6. `llm_backend/run.py`

## 4. 当前问题归纳

### 4.1 `memory` 模块问题

当前 `memory` 模块已经出现明显的横向拆分：

1. `simple_long_term_memory.py`
2. `memory_middleware.py`
3. `memory_extractor.py`
4. `ltm_*`
5. `stm_*`
6. `profile_*`
7. `memory_*_support.py`

这些拆分已经降低了单文件长度，但仍有两个结构问题：

1. “长期记忆存储、短期记忆存储、画像访问、记忆编排”四类能力没有在目录层级被清晰隔离。
2. 调用者需要先理解文件命名规则，才能推断功能位置。

### 4.2 `lg_agent` 模块问题

当前 `lg_agent` 目录已经形成下列文件簇：

1. 主图编排：`lg_builder.py`、`lg_nodes.py`、`lg_states.py`
2. ReAct：`lg_react.py`、`lg_react_support.py`、`lg_react_runtime.py`
3. 检索：`lg_retrievers.py`、`lg_retriever_support.py`、`lg_retriever_runtime.py`
4. 记忆桥接：`lg_context.py`、`lg_memory_runtime.py`、`lg_memory_prompt.py`
5. 模型与 Prompt：`lg_models.py`、`lg_model_support.py`、`lg_prompts.py`、`lg_prompt_support.py`

这说明代码已经在隐式按能力演化，但目录结构仍然是平铺的，导致：

1. 能力边界只能通过文件名前缀辨认。
2. 运行时、纯 helper、主流程入口都处在同一层。
3. 后续继续演化时，文件数量会继续增加，入口会越来越难扫读。

## 5. 总体方案

本次采用“按能力重新分包，但保留兼容入口”的中等强度重构方案。

原则如下：

1. 先把结构调整为按能力分包。
2. 旧入口暂时保留为兼容转发层。
3. 行为变化最小化，优先做结构迁移而不是逻辑重写。
4. 分阶段推进，先 `memory`，后 `lg_agent`。

## 6. 目标结构设计

### 6.1 `memory` 目标结构

```text
llm_backend/app/memory/
  config/
    __init__.py
    settings.py
    defaults.py
  stm/
    __init__.py
    store.py
    compressor.py
    utils.py
  ltm/
    __init__.py
    store.py
    collection.py
    search.py
    merge.py
    utils.py
  profile/
    __init__.py
    gateway.py
    cache.py
    utils.py
  orchestration/
    __init__.py
    middleware.py
    extractor.py
    runtime.py
  schemas.py
  facade.py
  __init__.py
```

#### 设计说明

1. `stm/` 负责 Redis 短期记忆相关能力。
2. `ltm/` 负责 Milvus 长期记忆相关能力。
3. `profile/` 负责用户画像读取、缓存与适配。
4. `orchestration/` 负责把 STM、LTM、画像和抽取器编排为统一记忆能力。
5. `config/` 负责记忆域的配置组织，避免配置、默认值和业务逻辑继续混放。
6. `facade.py` 负责对外暴露兼容入口，避免上层在第一阶段被迫一次性全部改完。

### 6.2 `lg_agent` 目标结构

```text
llm_backend/app/lg_agent/
  graph/
    __init__.py
    builder.py
    nodes.py
    edges.py
    state.py
    messages.py
  retrieval/
    __init__.py
    base.py
    registry.py
    kg.py
    rag.py
    summarize.py
    runtime.py
  react/
    __init__.py
    graph.py
    runtime.py
    helpers.py
  memory_bridge/
    __init__.py
    context.py
    prompt.py
    runtime.py
  modeling/
    __init__.py
    models.py
    prompts.py
    prompt_defaults.py
    prompt_loader.py
  facade.py
  __init__.py
```

#### 设计说明

1. `graph/` 只负责主图组装、节点和路由。
2. `retrieval/` 只负责统一检索能力与后端适配，不负责图流转。
3. `react/` 只负责 ReAct 子图和其运行时。
4. `memory_bridge/` 只负责 Agent 和记忆系统之间的桥接。
5. `modeling/` 只负责模型工厂、结构化输出模型和 Prompt 加载。
6. `facade.py` 负责对外稳定导出，避免第一阶段重构把 import 全部打碎。

## 7. 分阶段实施策略

### 7.1 第一期：`memory` 分包重组

第一期只处理 `memory`，不同时大动 `lg_agent` 主图。

#### 第一期目标

1. 将 `memory` 重构为 `config / stm / ltm / profile / orchestration` 五个清晰子包。
2. 保留旧入口模块作为兼容层。
3. 同步更新 `memory/README.md` 与根 README 中的结构说明。
4. 跑通 `tests/memory` 以及依赖记忆桥接的必要测试。

#### 第一期不做的事

1. 不重写记忆业务逻辑。
2. 不改记忆系统的外部行为契约。
3. 不同时改 `lg_agent` 内部所有调用方式。

### 7.2 第二期：`lg_agent` 分包重组

在 `memory` 稳定后，再推进 `lg_agent` 重构。

#### 第二期目标

1. 把 `lg_agent` 从平铺文件结构迁移为 `graph / retrieval / react / memory_bridge / modeling` 五个能力分包。
2. 清理旧的 `lg_*_support.py`、`lg_*_runtime.py` 横向扩散问题。
3. 保留旧的兼容导出，逐步迁移内部 import。
4. 同步更新 `lg_agent/README.md` 与根 README 中的结构说明。

#### 第二期不做的事

1. 不引入新的业务策略。
2. 不把 KG 子图整体推倒重写。
3. 不顺手把 `services` 或 `api` 做成新的分层实验。

## 8. 兼容策略

本次重构采用“新结构落地 + 旧入口转发”的兼容策略。

### 8.1 兼容原则

1. 先新增新包结构。
2. 再把旧模块变成薄转发层。
3. 内部 import 逐步迁移到新结构。
4. 在确认没有必要保留后，再删除旧入口。

### 8.2 兼容层形式

示例：

1. `app.memory.simple_long_term_memory`
   - 第一阶段保留
   - 内部转发到 `app.memory.ltm.store`

2. `app.memory.memory_middleware`
   - 第一阶段保留
   - 内部转发到 `app.memory.orchestration.middleware`

3. `app.lg_agent.lg_nodes`
   - 第二阶段初期可保留
   - 内部转发到 `app.lg_agent.graph.nodes`

### 8.3 不采用的兼容策略

本次不采用一次性全量改 import 的激进方式，原因如下：

1. 改动面过大。
2. 现有测试需要同时大规模迁移，回归成本高。
3. 在当前脏工作树下，不适合扩大非必要 diff。

## 9. 测试与验证策略

### 9.1 验证目标

结构重构后的验证不能只证明“代码能导入”，还要证明：

1. 能力边界更清晰。
2. 旧入口仍可兼容。
3. 行为没有发生非预期回归。

### 9.2 验证层次

1. 结构验证
   - 新目录是否按能力组织。
   - README 是否与真实目录一致。
   - 旧入口是否仍可导入。

2. 静态检查
   - `ruff check llm_backend/app llm_backend/scripts tests`

3. 单元测试
   - 第一期重点跑 `tests/memory`
   - 再补跑依赖记忆桥接的 `tests/lg_agent` 子集
   - 第二期重点跑 `tests/lg_agent`

### 9.3 测试文件处理约束

用户已明确要求：测试完成之后，测试文件要删除。

本次按如下规则执行：

1. 仓库原有测试文件不删除。
2. 仅删除本轮为了临时验证而新增的临时测试文件。
3. 若可以直接复用现有 `tests/` 目录完成验证，则不新增额外测试文件。
4. 如必须增加测试代码，优先写成稳定单测；若用户最终仍要求删除新增测试文件，则在验证完成后只删除本轮新增文件，不删除仓库既有测试。

该规则用于避免误删仓库现有测试资产。

## 10. 迁移顺序

### 10.1 统一顺序

1. 先做目录重排。
2. 再保留兼容转发层。
3. 再迁移内部 import。
4. 再更新 README 和结构文档。
5. 最后运行静态检查和测试。

### 10.2 推荐实施顺序

1. 第一期：`memory`
2. 第二期：`lg_agent`
3. 最后做少量外层 import 收口与文档整理

不建议反过来先改 `lg_agent`，因为它对 `memory` 有直接依赖，先改 `memory` 可以减少返工。

## 11. 风险与控制措施

### 11.1 主要风险

1. 兼容导出遗漏，导致旧 import 路径失效。
2. 目录迁移时产生循环依赖。
3. README 和真实结构不同步，导致文档误导。
4. 在当前已有大量未提交改动的工作树上，重构 diff 进一步放大。

### 11.2 控制措施

1. 每个阶段都先保留旧入口转发。
2. 每迁移一个能力包就跑对应测试，而不是等全部改完再跑。
3. 每个阶段结束同步更新对应 README。
4. 修改前后都查看 Git 状态，避免误覆盖用户已有改动。

## 12. 实施完成标准

### 12.1 第一期完成标准

1. `memory` 已按能力完成分包。
2. 旧 `memory` 入口仍能兼容导入。
3. `tests/memory` 通过。
4. 相关 README 已同步。

### 12.2 第二期完成标准

1. `lg_agent` 已按能力完成分包。
2. 旧 `lg_agent` 入口仍能兼容导入。
3. `tests/lg_agent` 通过。
4. 相关 README 已同步。

## 13. 推荐结论

推荐执行策略如下：

1. 采用中等强度的“能力分包 + 兼容层过渡”方案。
2. 第一期只做 `memory`。
3. 第二期再做 `lg_agent`。
4. 整个过程中优先复用现有测试，不额外制造临时测试文件。

这个方案能够在不推倒当前系统的前提下，真正改善阅读路径、目录表达力和后续维护成本。
