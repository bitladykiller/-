"""应用配置兼容入口。

职责：
- 作为 `app.shared.core.config` 的稳定导入路径
- 聚合设置模型、运行时门面和 URL helper 的公开 API

边界：
- 真实实现分别位于 `config_models.py` 和 `config_runtime.py`
- 这里不再承载具体设置定义和运行时组合逻辑
"""

from __future__ import annotations

from app.shared.core.config_models import (
    ServiceType,
)
from app.shared.core.config_runtime import settings

__all__ = [
    "ServiceType",
    "settings",
]
