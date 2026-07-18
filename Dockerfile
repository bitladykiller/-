FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖清单 + 应用代码（检索/解析模块已并入 app）
COPY requirements.txt ./requirements.txt
COPY app ./app
COPY configs ./configs

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

RUN mkdir -p /app/app/uploads \
    && chmod +x /app/configs/docker/app/start.sh

WORKDIR /app
