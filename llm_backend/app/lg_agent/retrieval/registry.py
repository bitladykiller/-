"""检索注册表兼容入口。"""

from app.lg_agent.lg_retriever_runtime import *  # noqa: F403
from app.lg_agent.lg_retrievers import get_retriever

__all__ = ["get_retriever"]
