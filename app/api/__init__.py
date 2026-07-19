"""API 路由注册入口。

这里只负责聚合各个子路由模块，不承载具体接口逻辑。
每个子路由文件已经声明了自己的 `tags`，这里不再重复配置。
"""

from app.api.conversations import router as conversations_router
from app.api.langgraph import router as langgraph_router
from app.api.upload import router as upload_router
from fastapi import APIRouter

api_router = APIRouter()

api_router.include_router(conversations_router)
api_router.include_router(upload_router)
api_router.include_router(langgraph_router)

__all__ = ["api_router"]
