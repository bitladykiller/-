"""应用容器 — 统一管理所有应用级依赖的生命周期。

职责：
- 在 lifespan 启动阶段按顺序初始化所有依赖
- 提供统一的依赖获取入口
- 关闭阶段释放所有外部连接

不负责：
- 具体依赖的创建逻辑（由各自的 factory 模块负责）
- 请求级依赖管理
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.chat.application.task_queue import _TaskManager
    from app.knowledge.infrastructure.orchestration.memory_middleware import MemoryMiddleware


@dataclass
class AppContainer:
    """应用级依赖容器。

    所有模块级单例（MemoryMiddleware、TaskManager、LLM 模型缓存等）
    统一收敛到此容器。测试时可以直接替换整个容器实例。
    """

    # ---- 记忆系统 ----
    memory_middleware: Any | None = None

    # ---- 任务管理 ----
    task_manager: Any | None = None

    # ---- LLM 模型实例（替代 models.py 中的 _models_cache 全局变量） ----
    llm_models: dict[str, Any] = field(default_factory=dict)

    # ---- 检索器运行时（替代 retriever_runtime 中的 _registry 等全局变量） ----
    retriever_registry: Any = None
    t2c_agent: Any = None
    cypher_example_retriever: Any = None

    _closed: bool = field(default=False, init=False)

    @classmethod
    async def build(cls, config: Any) -> "AppContainer":
        """按依赖顺序依次初始化所有组件。

        初始化顺序：
        1. LLM 模型实例
        2. 检索器运行时
        3. MemoryMiddleware
        4. TaskManager

        Args:
            config: 应用配置（settings 对象）

        Returns:
            初始化完成的 AppContainer 实例
        """
        container = cls()
        try:
            await container._init_task_manager(config)
            await container._init_memory_middleware()
            return container
        except Exception:
            await container.close()
            raise

    async def _init_task_manager(self, config: Any) -> None:
        """初始化后台任务管理器。"""
        from app.chat.application.task_queue import create_redis_client, _TaskManager

        self.task_manager = _TaskManager(create_redis_client(config.REDIS_URL))

    async def _init_memory_middleware(self) -> None:
        """初始化记忆中间件。"""
        from app.chat.infrastructure.memory_bridge.runtime import create_memory_middleware_instance

        self.memory_middleware = create_memory_middleware_instance()

    # ---- 生命周期管理 ----

    async def warm_up(self) -> None:
        """预热懒加载资源，减少首请求初始化延迟。

        目前主要预热 MemoryMiddleware。如果后续有其他需要预热的资源，在此扩展。
        """
        # MemoryMiddleware 已在 build() 中创建，这里只做显式的初始化确认
        if self.memory_middleware is None:
            await self._init_memory_middleware()

    async def close(self) -> None:
        """关闭所有外部连接（按依赖逆序）。

        保证即使某一步关闭失败，后续步骤也会继续执行。
        """
        if self._closed:
            return
        self._closed = True

        # 1. 关闭任务管理器
        if self.task_manager is not None:
            try:
                await self.task_manager.close()
            except Exception:
                pass
            self.task_manager = None

        # 2. 关闭记忆中间件
        if self.memory_middleware is not None:
            from app.chat.infrastructure.memory_bridge.runtime import close_memory_resources

            try:
                await close_memory_resources(self.memory_middleware)
            except Exception:
                pass
            self.memory_middleware = None


_container: AppContainer | None = None


async def get_container() -> AppContainer:
    """获取当前应用容器实例。"""
    global _container
    if _container is None:
        raise RuntimeError("AppContainer 尚未初始化，请先调用 AppContainer.build()")
    return _container


async def set_container(container: AppContainer) -> None:
    """设置当前应用容器实例。"""
    global _container
    _container = container


async def reset_container() -> None:
    """销毁当前应用容器实例（主要用于测试清理）。"""
    global _container
    if _container is not None:
        await _container.close()
        _container = None


__all__ = [
    "AppContainer",
    "get_container",
    "reset_container",
    "set_container",
]
