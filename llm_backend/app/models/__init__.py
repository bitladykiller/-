from app.models.user import User  # 最简模型（仅 id + username），为会话提供外键
from app.models.conversation import Conversation
from app.models.message import Message

# 导出所有模型类
__all__ = ["User", "Conversation", "Message"] 