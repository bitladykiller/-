"""平台层兼容包装，让旧导入路径 `app.shared` 继续生效。

新代码请直接使用 `app.platform.*` 路径。
"""

from app.platform.json_utils import extract_first_json_object, parse_first_json_object
from app.platform.security import wrap_user_message
from app.platform.container import AppContainer, get_container, reset_container, set_container

__all__ = [
    "extract_first_json_object",
    "parse_first_json_object",
    "wrap_user_message",
    "AppContainer",
    "get_container",
    "set_container",
    "reset_container",
]
