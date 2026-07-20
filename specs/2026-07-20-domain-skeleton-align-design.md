# 设计：业务域骨架对齐（方案 A）

- 日期：2026-07-20
- 状态：已批准待实现
- 分支：`refactor/domain-skeleton-align`

## 1. 目标

解决两类结构问题：

1. **两个 shared**：全局 `app/shared` 与 `app/chat/infrastructure/shared` 命名冲突、语义混淆。
2. **模块骨架不一致**：`user` 有标准 `domain/application/infrastructure`，`chat` 缺 `domain` 且 infra 命名不统一。

本阶段 **不改变业务行为**，只做目录骨架对齐与命名清理。

## 2. 非目标

- 不把 `knowledge` 拆成顶层 `memory` + `rag`
- 不合并两套 retrieval 实现
- 不把 `graph` / `kg` 挪到新顶层包
- 不修改 HTTP API 路径与运行时语义
- 不为「长得像」强建无用的空 `models/` / `repository/`

## 3. 统一骨架规则

每个业务域：

```text
app/<domain>/
  __init__.py
  README.md
  domain/              # 契约、schema、纯规则（不依赖 infrastructure）
  application/         # 用例门面
  infrastructure/      # 技术实现
    models/            # ORM（有则放）
    repository/        # 持久化（有则放）
    <feature>/         # 本域特性
```

非业务域：

| 包 | 角色 |
|----|------|
| `app/api` | HTTP 路由适配 |
| `app/shared` | **唯一**全局共享内核 |
| `app/platform` | 容器与生命周期 |
| `app/scripts` | Compose/运维脚本 |

依赖方向：

```text
api → application → domain
infrastructure → domain
shared ← 被上层消费
```

## 4. 分域落点

### 4.1 user

已基本符合模板。动作：

- 新增 `app/user/README.md`（标准样板说明）
- 不改 import 路径

### 4.2 knowledge

已有 `domain/` + `application/` + `infrastructure/`。动作：

- 新增 `app/knowledge/README.md`
- 说明 STM/LTM/向量持久化，**不**强建空 ORM repository
- 检查 `infrastructure/config`：有用则文档化，无用则删除

### 4.3 chat（主要改动）

| 项 | 动作 |
|----|------|
| `infrastructure/shared` | `git mv` → `infrastructure/utils` |
| import | `app.chat.infrastructure.shared` → `app.chat.infrastructure.utils` |
| `domain/` | 新建；最小 `schemas.py` 说明图状态留在 `infrastructure/graph/state` 的原因 |
| `AgentState` | **本阶段不迁移**（依赖 LangGraph，避免 domain 反向依赖 runtime） |

目标树：

```text
app/chat/
  domain/
  application/
  infrastructure/
    utils/           # 原 shared
    models/
    repository/
    graph/
    kg/
    react/
    retrievers/
    modeling/
  README.md
```

### 4.4 app/shared

保留为唯一全局 shared；新增 README，写明业务域禁止再命名 `shared`。

## 5. 实现步骤

1. 从 `main` 拉分支 `refactor/domain-skeleton-align`
2. 重命名 chat `shared` → `utils` 并更新全部引用
3. 新建 `chat/domain`
4. 补齐 `user` / `knowledge` / `chat` / `shared` / 根 `app` 的 README
5. 更新 `docs/架构概览.md`、`docs/迁移指南.md`（本地 docs 目录）、`CHANGELOG.md`
6. `pytest` + `ruff check app tests`
7. 按仓库 Git 规范：推送分支 → 合并 main → 推送 main

## 6. 验收标准

1. 全仓不存在 `app/chat/infrastructure/shared`
2. `user` / `knowledge` / `chat` 均具备 `domain/` + `application/` + `infrastructure/`
3. 全量 pytest 通过；无行为变更
4. 文档与目录一致
5. 不为对齐而新增无用空目录

## 7. 风险与回滚

- 风险：漏改 import 导致运行时 `ImportError`
- 缓解：全量 pytest + rg 检查旧路径
- 回滚：revert 合并提交

## 8. 自检

- [x] 无 TBD 占位
- [x] 与方案 A 一致：骨架对齐 + 去假 shared
- [x] 范围可单 PR 完成
- [x] `AgentState` 去留已明确（不迁）
