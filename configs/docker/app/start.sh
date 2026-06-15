#!/bin/sh
set -eu

echo "初始化 MySQL 表结构..."
cd /app
python -c "import asyncio; from app.scripts.bootstrap_compose_db import create_all_tables; asyncio.run(create_all_tables())"

echo "启动 FastAPI 服务..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
