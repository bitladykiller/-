"""LTM 软删记录的定时硬清理调度。

业务删会话仍 soft_delete；本模块在后台周期调用
SimpleLongTermMemory.hard_purge_soft_deleted，回收 Milvus 空间。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.shared.core.app_config import LTMPurgeConfig

logger = logging.getLogger(__name__)

LtmProvider = Callable[[], Any | None]
# 返回 True 表示应停止循环
WaitFn = Callable[[asyncio.Event, float], Awaitable[bool]]


async def _wait_for_stop_or_timeout(stop_event: asyncio.Event, timeout: float) -> bool:
    """等待 stop 或超时。返回 True 表示收到 stop。"""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def run_ltm_hard_purge_loop(
    *,
    get_ltm: LtmProvider,
    purge_config: LTMPurgeConfig,
    stop_event: asyncio.Event,
    wait_fn: WaitFn | None = None,
) -> None:
    """循环执行硬清理，直到 stop_event 被 set。

    每轮：等待 interval（可被 stop 打断）→ purge 一次。
    """
    if not purge_config.enabled:
        logger.info("LTM 硬清理调度未启用（purge.enabled=false）")
        return

    interval = max(1, int(purge_config.interval_seconds))
    wait = wait_fn or _wait_for_stop_or_timeout
    logger.info(
        "LTM 硬清理调度已启动 | interval=%ss retention=%ss",
        interval,
        purge_config.retention_seconds,
    )

    while not stop_event.is_set():
        if await wait(stop_event, float(interval)):
            break

        ltm = get_ltm()
        if ltm is None or not hasattr(ltm, "hard_purge_soft_deleted"):
            logger.debug("LTM 未就绪，跳过本轮硬清理")
            continue

        try:
            deleted = await ltm.hard_purge_soft_deleted(
                retention_seconds=purge_config.retention_seconds,
                batch_limit=purge_config.batch_limit,
            )
            if deleted:
                logger.info("LTM 定时硬清理 | deleted=%s", deleted)
        except Exception:
            logger.exception("LTM 定时硬清理执行失败")

    logger.info("LTM 硬清理调度已停止")


__all__ = ["run_ltm_hard_purge_loop"]
