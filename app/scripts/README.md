# Scripts 模块说明

`app/scripts/` 放的是通过 `python -m app.scripts.xxx` 执行的维护脚本。这里的脚本偏向开发和部署辅助，不承载线上请求流程。

## 结构分工

- `init_db.py`
  - 本地开发用的重置脚本。
  - 会先删表再建表，适合开发环境快速重置，不适合容器启动链路。
- `bootstrap_compose_db.py`
  - Docker Compose 启动时的建表脚本。
  - 只执行 `create_all`，不删除已有数据。

## 当前边界

- `scripts/` 只放“手动执行或启动时执行”的辅助脚本，不承载 API、服务层或 Agent 主流程逻辑。
- 如果某段逻辑需要被业务代码长期复用，应优先下沉到 `app/` 内部模块，而不是继续堆在脚本文件里。

## 后续维护建议

- 新增脚本时，先判断它属于“本地开发辅助”还是“部署启动辅助”，避免不同用途的脚本混在一起。
- 如果多个脚本再次出现明显重复且长期共同演进，再考虑重新抽出共享 helper；在重复规模很小时，优先保持脚本自包含。

## 数据库迁移提示

Compose 挂载的 `configs/mysql-init/*.sql` 仅在 MySQL **首次初始化数据卷**时自动执行。
已有环境升级到 Redis Stream Inbox 幂等消费前，需手工运行：

```bash
mysql -u <user> -p <database> < configs/mysql-init/migration_stream_idempotency.sql
```

该迁移补充 `messages.turn_event_id`、对应唯一键与 `processed_events` Inbox 表；执行前
请按常规发布流程备份数据库并在维护窗口验证。
