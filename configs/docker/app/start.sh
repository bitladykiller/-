#!/bin/sh
set -eu

echo "初始化 MySQL 表结构..."
cd /app
python -m app.scripts.bootstrap_compose_db

echo "启动 FastAPI 服务..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
