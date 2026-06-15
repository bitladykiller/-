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
3. 自动完成数据库建表
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

项目当前只保留 `docker compose` 这一种应用启动方式，不再提供本地直启入口。

数据库维护脚本属于内部维护能力，不作为项目启动入口对外提供。

## 3. 验证

### 3.1 健康检查

```bash
curl http://localhost:8000/health
```

### 3.2 结构与测试

```bash
pytest tests/core/test_lazy_package_imports.py
pytest
```

## 4. 生产建议

- 只对宿主机暴露 `8000` 端口
- API Key 和数据库凭据只通过环境变量注入
- 如需扩容，优先把应用层做成无状态，再横向扩容容器
- 上传文件目录默认位于 `app/uploads/`，容器内由 `app_uploads` 卷持久化
