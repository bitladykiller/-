from typing import List, Dict
from app.core.database import AsyncSessionLocal
from app.models.conversation import Conversation, DialogueType
from sqlalchemy import select
from app.core.logger import get_logger

logger = get_logger(__name__)


class ConversationService:
    @staticmethod
    def get_conversation_title(message: str, max_length: int = 20) -> str:
        """从消息中提取会话标题"""
        title = " ".join(message.split())
        if len(title) > max_length:
            title = title[:max_length] + "..."
        return title

    @staticmethod
    async def create_conversation(user_id: int) -> int:
        """创建新会话"""
        async with AsyncSessionLocal() as db:
            conversation = Conversation(
                user_id=user_id,
                title="新会话",
                dialogue_type=DialogueType.NORMAL
            )
            db.add(conversation)
            await db.commit()
            await db.refresh(conversation)

            return conversation.id

    @staticmethod
    async def get_user_conversations(user_id: int) -> List[Dict]:
        """获取用户的所有会话"""
        try:
            async with AsyncSessionLocal() as db:
                # 查询用户的所有会话，排除标题为"新会话"的对话
                stmt = select(Conversation).where(
                    Conversation.user_id == user_id,
                    Conversation.title != "新会话"
                ).order_by(Conversation.created_at.desc())

                result = await db.execute(stmt)
                conversations = result.scalars().all()

                return [
                    {
                        "id": conv.id,
                        "title": conv.title,
                        "created_at": conv.created_at.isoformat(),
                        "status": conv.status,
                        "dialogue_type": conv.dialogue_type.value
                    }
                    for conv in conversations
                ]

        except Exception as e:
            logger.error(
                f"get_user_conversations 异常 | user_id={user_id} | {e}",
                exc_info=True,
            )
            raise

    @staticmethod
    async def delete_conversation(conversation_id: int):
        """删除会话及其所有消息"""
        try:
            async with AsyncSessionLocal() as db:
                # 查询会话
                stmt = select(Conversation).where(Conversation.id == conversation_id)
                result = await db.execute(stmt)
                conversation = result.scalar_one_or_none()

                if not conversation:
                    raise ValueError(f"Conversation {conversation_id} not found")

                # 删除会话(会自动级联删除相关消息)
                await db.delete(conversation)
                await db.commit()

        except Exception as e:
            logger.error(
                f"delete_conversation 异常 | conversation_id={conversation_id} | {e}",
                exc_info=True,
            )
            raise

    @staticmethod
    async def update_conversation_name(conversation_id: int, name: str):
        """更新会话名称"""
        try:
            async with AsyncSessionLocal() as db:
                # 查询会话
                stmt = select(Conversation).where(Conversation.id == conversation_id)
                result = await db.execute(stmt)
                conversation = result.scalar_one_or_none()

                if not conversation:
                    raise ValueError(f"Conversation {conversation_id} not found")

                # 更新名称
                conversation.title = name
                await db.commit()

        except Exception as e:
            logger.error(
                f"update_conversation_name 异常 | conversation_id={conversation_id} "
                f"name={name} | {e}",
                exc_info=True,
            )
            raise
