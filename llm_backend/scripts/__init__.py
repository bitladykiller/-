"""项目脚本入口集合。

当前主要包含数据库初始化等运维脚本，不承载业务逻辑。
- `init_db.py`：本地开发时重置表结构。
- `bootstrap_compose_db.py`：Docker Compose 启动时只建表，不删数据。
- `db_script_support.py`：脚本共享的导入路径和 metadata 注册 helper。
"""
