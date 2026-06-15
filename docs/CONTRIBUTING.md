# 开发规范

## 1. 代码风格

### 1.1 Python 版本
- 最低版本：Python 3.10
- 使用类型注解
- 使用 Pydantic v2

### 1.2 格式化工具
- 使用 **Ruff** 进行代码检查和格式化
- 行长度限制：100 字符
- 目标版本：py310

### 1.3 导入顺序
按照 Ruff 的默认规则（isort）：
1. 标准库
2. 第三方库
3. 本地模块

```python
import logging
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from app.shared.core.config import settings
from app.chat.infrastructure.graph.state import AgentState
```

### 1.4 命名规范
- **模块**：snake_case（如 `agent_state.py`）
- **类**：PascalCase（如 `AgentState`）
- **函数/方法**：snake_case（如 `get_memory_state`）
- **常量**：UPPER_SNAKE_CASE（如 `MAX_RETRY_COUNT`）
- **私有属性**：前缀 `_`（如 `_internal_state`）

## 2. 类型注解

### 2.1 函数签名
所有公开函数必须使用类型注解：

```python
def process_message(
    message: str,
    user_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    ...
```

### 2.2 Pydantic 模型
使用 Pydantic v2 语法：

```python
from pydantic import BaseModel

class Conversation(BaseModel):
    id: str
    title: str
    created_at: datetime
    
    model_config = {"extra": "forbid"}
```

## 3. 文档注释

### 3.1 模块级文档
每个模块必须有模块级文档：

```python
"""模块职责说明。

这里只做一件事：
- 简要描述模块职责

边界：
- 不做什么
- 和其他模块的关系
"""
```

### 3.2 函数文档
重要函数必须有文档字符串：

```python
def get_memory_state(user_id: str) -> AgentMemoryState:
    """获取用户记忆状态。
    
    Args:
        user_id: 用户唯一标识
        
    Returns:
        AgentMemoryState: 包含短期记忆、长期记忆和用户画像
        
    Raises:
        ValueError: 如果 user_id 为空
    """
    ...
```

## 4. 错误处理

### 4.1 异常类型
使用明确的异常类型：
- `ValueError`：参数验证错误
- `NotFoundError`：资源不存在
- `ServiceError`：服务层错误

### 4.2 日志记录
使用结构化日志：

```python
from app.shared.core.logger import get_logger, format_log_context

logger = get_logger(__name__)

try:
    result = process_message(...)
    logger.info(format_log_context("消息处理完成", user_id=user_id))
except Exception as e:
    logger.error(format_log_context("消息处理失败", error=str(e)))
    raise
```

## 5. 提交规范

### 5.1 Commit Message 格式
遵循 Conventional Commits：

```
<type>(<scope>): <subject>

<body>

<footer>
```

### 5.2 Type 类型
- `feat`: 新功能
- `fix`: Bug 修复
- `refactor`: 重构
- `docs`: 文档更新
- `test`: 测试相关
- `chore`: 构建/配置相关

### 5.3 示例
```
feat(chat): 新增 RAG 检索策略

- 添加 MilvusDocRetriever 实现
- 注册到 retriever_registry
- 支持 hybrid 检索模式

Refs: #123
```

## 6. 分层约束

### 6.1 Domain 层
- 纯业务逻辑
- 不依赖外部框架（FastAPI、SQLAlchemy 等）
- 不直接访问数据库

### 6.2 Application 层
- 用例编排
- 协调领域对象
- 不包含技术细节

### 6.3 Infrastructure 层
- 技术实现
- 数据库访问
- 外部服务调用

### 6.4 Interface 层
- API 路由
- 请求/响应转换
- 不包含业务逻辑

## 7. 测试规范

### 7.1 测试类型
- **单元测试**：domain 层纯业务逻辑
- **集成测试**：infrastructure 层与外部服务
- **端到端测试**：完整 API 流程

### 7.2 测试命名
```python
def test_get_memory_state_returns_correct_state():
    ...

def test_process_message_raises_error_when_user_id_empty():
    ...
```

### 7.3 测试文件位置
```
tests/
├── unit/
│   ├── chat/
│   └── knowledge/
├── integration/
│   ├── chat/
│   └── knowledge/
└── e2e/
    └── api/
```

## 8. 依赖注入

### 8.1 使用 FastAPI Depends
```python
from fastapi import Depends
from app.shared.core.database import AsyncSessionLocal

async def get_session():
    async with AsyncSessionLocal() as session:
        yield session

@router.post("/chat")
async def chat(
    request: ChatRequest,
    session: AsyncSession = Depends(get_session),
):
    ...
```

### 8.2 避免全局状态
不要使用全局变量存储状态，使用依赖注入。

## 9. 安全规范

### 9.1 Prompt 注入防护
使用 `wrap_user_message` 包裹用户输入：

```python
from app.chat.infrastructure.graph.message_utils import wrap_user_message

wrapped = wrap_user_message(user_input)
```

### 9.2 敏感信息
- 不在日志中输出敏感信息
- 不在配置文件中硬编码密码
- 使用环境变量存储 API Key

## 10. 性能规范

### 10.1 异步操作
使用 `async/await` 处理 I/O 操作：

```python
async def get_user(user_id: str) -> User:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        return result.scalar_one()
```

### 10.2 批量操作
避免 N+1 查询，使用批量操作：

```python
# 错误：N+1 查询
for user_id in user_ids:
    user = await get_user(user_id)
    
# 正确：批量查询
users = await get_users(user_ids)
```

## 11. 文档更新

每次修改代码后，检查是否需要更新：
- README.md
- ARCHITECTURE.md
- 模块级 README（如有）

## 12. Git 工作流

### 12.1 分支命名
- `feature/xxx`: 新功能
- `fix/xxx`: Bug 修复
- `refactor/xxx`: 重构

### 12.2 工作流程
1. 从 main 分支创建新分支
2. 完成开发和测试
3. 推送分支到远程
4. 合并 main 到新分支（解决冲突）
5. 合并新分支到 main
6. 推送 main 分支
