"""`lg_models.py` 共享的运行时 helper。

职责：
- 承接模型角色枚举、温度映射和 provider 工厂选择 helper
- 承接懒加载代理和缓存写入这类与具体模型 provider 无关的运行时细节

边界：
- 不直接读取全局 settings
- 不直接创建 DeepSeek / Ollama 模型实例
- 不定义节点结构化输出模型
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, TypeAlias

ModelRole = Literal[
    "agent",
    "router",
    "retrieval_plan",
    "guardrails",
    "cypher",
    "react",
    "react_judge",
]
ModelFactory: TypeAlias = Callable[[float], Any]
ModelResolver: TypeAlias = Callable[[ModelRole, float], Any]

MODEL_TEMPERATURES: dict[ModelRole, float] = {
    "agent": 0.7,
    "router": 0.1,
    "retrieval_plan": 0.1,
    "guardrails": 0.1,
    "cypher": 0.2,
    "react": 0.4,
    "react_judge": 0.1,
}


def resolve_model_factory(
    service_type: str,
    *,
    deepseek_factory: ModelFactory,
    ollama_factory: ModelFactory,
) -> ModelFactory:
    """根据 provider 名称选择当前运行时要用的模型工厂。"""
    if service_type == "deepseek":
        return deepseek_factory
    return ollama_factory


def get_or_create_cached_model(
    cache: dict[ModelRole, Any],
    name: ModelRole,
    creator: Callable[[], Any],
) -> Any:
    """按角色名获取缓存模型，不存在时才执行 creator。"""
    if name not in cache:
        cache[name] = creator()
    return cache[name]


def lazy_model_repr(name: ModelRole, temperature: float) -> str:
    """构造懒代理的稳定字符串表示。"""
    return f"_LazyModel(name={name}, t={temperature})"


class LazyModelProxy:
    """延迟代理：访问属性/方法时才真正创建模型。"""

    __slots__ = ("_name", "_temperature", "_resolver")

    def __init__(
        self,
        name: ModelRole,
        temperature: float,
        resolver: ModelResolver,
    ) -> None:
        self._name = name
        self._temperature = temperature
        self._resolver = resolver

    def _get(self) -> Any:
        """返回底层模型实例。"""
        return self._resolver(self._name, self._temperature)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._get(), item)

    def __bool__(self) -> bool:
        """总是返回 True，避免代理在条件判断里表现反常。"""
        return True

    def __await__(self):
        """支持 `await lazy_model`，直接代理到底层模型。"""
        return self._get().__await__()

    def __str__(self) -> str:
        return lazy_model_repr(self._name, self._temperature)

    def __repr__(self) -> str:
        return self.__str__()


def build_lazy_model(name: ModelRole, resolver: ModelResolver) -> LazyModelProxy:
    """按角色名创建懒加载代理，统一温度来源。"""
    return LazyModelProxy(name, MODEL_TEMPERATURES[name], resolver)


__all__ = [
    "LazyModelProxy",
    "MODEL_TEMPERATURES",
    "ModelFactory",
    "ModelRole",
    "build_lazy_model",
    "get_or_create_cached_model",
    "lazy_model_repr",
    "resolve_model_factory",
]
