"""模型包入口。

职责：
- 集中导出当前仍在使用的 SQLAlchemy 模型
- 给建表脚本和服务层提供稳定导入入口

边界：
- 这里只做模型聚合，不承载查询逻辑和业务规则
"""

from app.models.conversation import Conversation
from app.models.user import User

__all__ = ["User", "Conversation"]
