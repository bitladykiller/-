# AssistGen Frontend

工程化 Vue 3 前端（Vite + TypeScript + Pinia + Vue Router），与后端 **前后端分离**。

## 本地开发

```bash
# 终端 1：后端
docker compose up -d app   # 或完整栈

# 终端 2：前端
cd frontend
npm install
npm run dev
```

浏览器：`http://localhost:5173`  
Vite 将 `/api`、`/health` 代理到 `http://127.0.0.1:8000`。

## Docker（推荐）

```bash
# 仓库根目录
docker compose up -d --build
```

- 前端：`http://localhost:8080`
- 后端直连：`http://localhost:8000`
- OpenAPI：`http://localhost:8080/docs`（经 nginx 反代）

Nginx 将 `/api`、`/health`、`/docs` 转发到 `app:8000`，前端请求使用相对路径。

## 知识文档 UI

顶栏「知识文档」打开抽屉（`UploadDrawer.vue`）：

| 标签 | 行为 |
|------|------|
| 上传新文档 | `POST /api/upload` `mode=create` → 轮询 task |
| 我的文档 / 更新 | `GET /api/documents/user/{userId}` 列表；更新时固定该行 `doc_id` + `mode=replace` |
| hash 未变 | 响应 `unchanged/skipped`，不轮询 task |

## 环境变量

见 `.env.example`。生产镜像构建时 `VITE_API_BASE_URL` 为空（同源反代）。
