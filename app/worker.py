"""独立事件 worker 入口。

用法（与 app 同镜像，不同 command）：

    python -m app.worker

配套：app 容器设 `EVENTS_INLINE_CONSUMER=0` 关闭内嵌消费，
本进程专职消费 `agent:events`（文档索引、回合后处理），
API 进程从此不再兼职跑 CPU 密集的 PDF 解析。

单容器部署不需要它——容器默认内嵌消费者，行为等价。
"""

from __future__ import annotations

import asyncio
import signal

from app.shared.core.logger import get_logger, setup_logging

logger = get_logger(__name__)


async def run_worker() -> None:
    """构建容器、运行事件消费循环直到收到退出信号。"""
    from app.platform.container import AppContainer, reset_container, set_container
    from app.platform.events import build_core_handlers
    from app.shared.core.config import settings

    container = await AppContainer.build(settings)
    await set_container(container)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - 非 Unix 平台
            pass

    queue = container.event_queue
    if queue is None:
        raise RuntimeError("事件队列未初始化，worker 无法启动")

    logger.info("worker 启动，开始消费事件")
    try:
        await queue.run_consumer(build_core_handlers(), stop_event)
    finally:
        logger.info("worker 退出，释放资源")
        await reset_container()


def main() -> None:
    setup_logging()
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
