FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先复制依赖清单和本地包目录，尽量复用 pip 安装缓存层。
COPY requirements.txt ./requirements.txt
COPY shared_retrieval ./shared_retrieval
COPY rag_doc_parser ./rag_doc_parser

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# 复制新的 app 目录（DDD 架构）
COPY app ./app
COPY configs ./configs

RUN mkdir -p /app/app/uploads

WORKDIR /app

# 这个镜像只供 docker-compose.yml 覆盖 command 后使用，不支持脱离 Compose 单独拉起。
CMD ["sh", "-lc", "echo 'This image must be started via docker compose up -d --build.' >&2; exit 1"]
