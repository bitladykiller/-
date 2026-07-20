# app.shared — 唯一全局共享内核

## 职责

- `core/`：配置、数据库、日志、JSON 工具
- `security/`：Prompt 防护
- `retrieval/`：Milvus 混合检索公共核
- `task_queue.py`：后台任务队列

## 规则

1. **全项目只有一个 shared**：`app.shared`
2. 业务域（chat/knowledge/user）内 **禁止** 再命名目录为 `shared`
3. 业务域跨节点小工具放在该域 `infrastructure/utils`
4. shared **不**依赖 chat/knowledge/user
