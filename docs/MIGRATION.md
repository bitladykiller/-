# 迁移指南

## 1. 当前状态

迁移已经收口完成：

- 新代码只使用 `app/` 下的真实目录
- 旧兼容目录和旧顶层入口已经删除
- 旧导入路径不再被支持

## 2. 当前推荐导入

### 基础设施

```python
from app.shared.core.config import settings
from app.shared.core.database import Base
from app.shared.security import wrap_user_message
```

### 对话能力

```python
from app.chat.infrastructure.graph.builder import graph
from app.chat.infrastructure.retrievers.retriever_runtime import get_retriever
```

### 记忆能力

```python
from app.knowledge.infrastructure.stm.redis_short_term_memory import RedisShortTermMemory
from app.knowledge.infrastructure.ltm.simple_long_term_memory import SimpleLongTermMemory
from app.knowledge.domain.schemas import AgentMemoryState
```

### 用户能力

```python
from app.user.infrastructure.models.user import User
from app.user.infrastructure.models.conversation import Conversation
from app.user.application.user_profile_service import UserProfileService
```

## 3. 已移除的旧路径

以下路径现在都应视为历史路径：

```python
from app.core import ...
from app.security import ...
from app.models import ...
from app.lg_agent import ...
from app.memory import ...
from app.services import ...
```

如果仍然出现这些路径，需要直接改到真实模块位置。

## 4. 检查建议

结构整理后建议运行：

```bash
python scripts/verify_migration.py
pytest
ruff check app scripts tests
```
