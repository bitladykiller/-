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

COPY llm_backend ./llm_backend
COPY docker ./docker

RUN mkdir -p /app/llm_backend/uploads \
    && chmod +x /app/docker/app/start.sh

WORKDIR /app/llm_backend

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
