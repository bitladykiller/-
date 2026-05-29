import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
sys.path.append(str(ROOT_DIR))

import asyncio
from app.core.database import engine, Base
from app.models import User, Conversation, Message  # User 为最简模型（无 auth 字段）


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


def main():
    try:
        asyncio.run(init_db())
    except Exception as e:
        print(f"Database initialization failed: {e}")


if __name__ == "__main__":
    main()