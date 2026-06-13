"""配置模块共享 helper。

职责：
- 收敛 `Settings` 对多个子配置对象的字段解析逻辑
- 收敛数据库 / Redis / Milvus 连接地址的纯字符串组装逻辑

边界：
- 不读取环境变量
- 不定义具体的 Settings 类
- 不持有全局 `settings` 单例
"""

from __future__ import annotations

from typing import Any


def resolve_setting_from_sources(name: str, sources: tuple[object, ...]) -> Any:
    """从多个子配置对象中按顺序查找字段值。"""
    for source in sources:
        model_fields = getattr(source.__class__, "model_fields", {})
        if name in model_fields:
            return getattr(source, name)
    raise AttributeError(f"'Settings' object has no attribute '{name}'")


def build_database_url(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> str:
    """构建 MySQL 异步连接 URL。"""
    return f"mysql+aiomysql://{user}:{password}@{host}:{port}/{database}"


def build_redis_url(
    *,
    host: str,
    port: int,
    db: int,
    password: str,
) -> str:
    """构建 Redis 连接 URL。"""
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{host}:{port}/{db}"


def build_milvus_url(
    *,
    host: str,
    port: int,
) -> str:
    """构建 Milvus 连接地址。"""
    return f"{host}:{port}"


__all__ = [
    "build_database_url",
    "build_milvus_url",
    "build_redis_url",
    "resolve_setting_from_sources",
]
