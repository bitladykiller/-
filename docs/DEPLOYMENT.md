# 部署指南

## 1. 环境变量

把根目录模板复制到 `app/.env`：

```bash
cp .env.example app/.env
```

容器部署还会额外叠加根目录的 `.env.docker`。

## 2. Docker Compose

### 2.1 启动

```bash
docker compose up -d --build
```

启动流程会自动完成：

1. 启动 MySQL、Neo4j、Redis、Milvus 及其依赖
2. 执行 Neo4j 导入任务（存在 CSV 时）
3. 执行 `python -m app.scripts.bootstrap_compose_db`
4. 启动 FastAPI 服务

### 2.2 常用命令

```bash
docker compose ps
docker compose logs app
docker compose down
docker compose down -v
```

### 2.3 访问地址

- API：`http://localhost:8000`
- Swagger：`http://localhost:8000/docs`

## 3. 本地开发启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.run
```

## 4. 数据库维护脚本

### 4.1 只建表

```bash
python -m app.scripts.bootstrap_compose_db
```

### 4.2 本地重置表

```bash
python -m app.scripts.init_db
```

## 5. 验证

### 5.1 健康检查

```bash
curl http://localhost:8000/health
```

### 5.2 结构与测试

```bash
python scripts/verify_migration.py
pytest
```

## 6. 生产建议

- 只对宿主机暴露 `8000` 端口
- API Key 和数据库凭据只通过环境变量注入
- 如需扩容，优先把应用层做成无状态，再横向扩容容器
- 上传文件目录默认位于 `app/uploads/`，容器内由 `app_uploads` 卷持久化
