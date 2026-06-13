#!/bin/sh
set -eu

echo "初始化 MySQL 表结构..."
cd /app
python llm_backend/scripts/bootstrap_compose_db.py

echo "启动 FastAPI 服务..."
cd /app/llm_backend
exec uvicorn main:app --host 0.0.0.0 --port 8000
