"""记忆模块静态配置。

这个模块负责：
- 集中声明 STM（Short-Term Memory，短期记忆）和
  LTM（Long-Term Memory，长期记忆）的默认参数
- 给 Redis 窗口、压缩策略、Milvus 检索策略提供唯一配置源

这个模块不负责：
- 运行时读取环境变量
- Redis / Milvus 客户端初始化
- 具体的记忆读写逻辑
"""

import re
from typing import Any

SHORT_TERM_MEMORY_CONFIG: dict[str, Any] = {
    "redis": {
        "key_prefix": "agent:stm",
        "ttl_seconds": 86400,
        "lock_ttl_seconds": 10,
    },
    "window": {
        "max_messages": 16,
    },
    "time_window_seconds": 86400,
    "compression": {
        "trigger_rounds": 6,
        "trigger_messages": 20,
        "keep_recent_rounds": 4,
    },
}

LONG_TERM_MEMORY_CONFIG: dict[str, Any] = {
    "collection_name": "customer_agent_long_memory",
    "search": {
        "top_k": 5,
        "score_threshold": 0.72,
    },
    "deduplication": {
        "top_k": 3,
        "similarity_threshold": 0.88,
    },
}
LONG_TERM_MEMORY_TYPE_VALUES = frozenset({"issue_history", "solution_note"})
COMPILED_SENSITIVE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"password|密码|passwd",
        r"验证码|verification.code|captcha",
        r"\d{17}[\dXx]",
        r"\d{16,19}",
        r"token|secret|access.key|api.key",
        r"1[3-9]\d{9}",
    )
)
