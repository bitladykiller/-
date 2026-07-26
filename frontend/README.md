# AssistGen Frontend

工程化 Vue 3 前端（Vite + TypeScript + Pinia + Vue Router），与后端 **前后端分离**。

## 设计系统 — 「鎏金礼宾 · Art Deco Noir」

界面遵循统一的装饰艺术（Art Deco）视觉语言，代码集中在 `src/styles/main.css`（设计令牌 + 装饰工具类）与 `src/App.vue`（全局氛围层）：

| 元素 | 说明 |
|------|------|
| 配色 | 墨玉近黑底（`--noir`）、漆面面板（`--lacquer*`）、香槟鎏金强调（`--gold*`）、象牙文本（`--ivory*`）、玉石辅色（`--jade`/`--garnet`） |
| 字体 | 标题 `Marcellus` + `Noto Serif SC`；正文 `Albert Sans` + `Noto Sans SC`；等宽 `Fragment Mono`（Google Fonts，`index.html` 引入） |
| 纹样 | 扇形放射纹（`repeating-conic-gradient`）、菱格暗纹、四角鎏金包边（`.deco-corners`）、菱形宝石状态灯（`.gem`） |
| 氛围 | 极光金雾漂移、胶片颗粒、边缘暗角（`App.vue` + `body::before`），均为纯 CSS |
| 动效 | 逐层揭幕（`.reveal` + `--d` 延迟）、按钮扫光、流式扇形转针（`.fan-spinner`）、鎏金光标；整体尊重 `prefers-reduced-motion` |

装饰全部由 CSS 渐变 / 内联 SVG data URI 实现，**未新增任何运行时依赖**。

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
