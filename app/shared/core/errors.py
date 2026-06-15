"""跨层业务异常。

这个模块负责：
- 定义与传输协议无关的业务异常类型
- 让 Service / Repository 层表达"资源不存在"这类语义，而不感知 HTTP

这个模块不负责：
- HTTP 状态码映射（由 API 层的 run_api_action 统一完成）
- 日志记录

WHY 需要它：
Repository 曾用裸 `ValueError` 表达"会话不存在"，API 层的统一异常包装
把一切非 HTTPException 翻成 500 —— 于是"删除一个不存在的会话"返回
500 Internal Server Error 而不是 404，前端无法区分"资源已没了"和"服务器坏了"。
"""

from __future__ import annotations


class ResourceNotFoundError(Exception):
    """资源不存在，或不属于当前请求方。

    归属校验失败也统一用本异常（而不是单独的 403）：
    在无鉴权体系下区分"不存在"与"不是你的"只会方便他人枚举资源 ID。
    API 层统一映射为 HTTP 404。
    """

    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(message)
        self.message = message


__all__ = ["ResourceNotFoundError"]
