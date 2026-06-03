"""Agent 工具函数。

v3.17: 清理死代码。移除了 reduce_docs / format_docs / interrupt，
这些函数未被任何生产代码调用（LangGraph 状态更新由 add_messages reducer 处理）。
"""
import uuid


def new_uuid() -> str:
    """生成 UUID4 字符串。"""
    return str(uuid.uuid4())