"""LTM 软删记录硬清理与调度测试。"""

from __future__ import annotations

import asyncio

from app.knowledge.infrastructure.ltm.purge_scheduler import run_ltm_hard_purge_loop
from app.knowledge.infrastructure.ltm.simple_long_term_memory import SimpleLongTermMemory
from app.shared.core.app_config import LTMPurgeConfig


class FakeMilvusClient:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.query_filter = ""
        self.delete_filter = ""
        self._rows = rows if rows is not None else [
            {"memory_id": "old-1"},
            {"memory_id": "old-2"},
        ]

    def has_collection(self, collection_name: str) -> bool:
        return True

    def query(
        self,
        *,
        collection_name: str,
        filter: str,
        output_fields: list[str],
        limit: int,
    ):
        self.query_filter = filter
        return list(self._rows)

    def upsert(self, *, collection_name: str, data: list[dict]) -> None:
        return None

    def delete(self, *, collection_name: str, filter: str) -> None:
        self.delete_filter = filter


class FakeEmbedding:
    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2]


def test_hard_purge_soft_deleted_calls_milvus_delete() -> None:
    client = FakeMilvusClient()
    ltm = SimpleLongTermMemory(
        milvus_client=client,  # type: ignore[arg-type]
        embedding_model=FakeEmbedding(),  # type: ignore[arg-type]
        collection_name="ltm_test",
    )
    ltm._now_ts = lambda: 1_700_000_000  # type: ignore[method-assign]

    deleted = asyncio.run(
        ltm.hard_purge_soft_deleted(retention_seconds=86400, batch_limit=100)
    )

    assert deleted == 2
    assert "is_deleted == true" in client.query_filter
    assert "updated_at <" in client.query_filter
    assert "old-1" in client.delete_filter
    assert "old-2" in client.delete_filter
    assert "1699913600" in client.query_filter


def test_hard_purge_returns_zero_when_no_rows() -> None:
    client = FakeMilvusClient(rows=[])
    ltm = SimpleLongTermMemory(
        milvus_client=client,  # type: ignore[arg-type]
        embedding_model=FakeEmbedding(),  # type: ignore[arg-type]
        collection_name="ltm_test",
    )

    deleted = asyncio.run(ltm.hard_purge_soft_deleted(retention_seconds=0))
    assert deleted == 0
    assert client.delete_filter == ""


def test_purge_loop_runs_once_with_injected_wait() -> None:
    """用注入 wait_fn 避免真实 sleep：首次返回 False 触发 purge，再返回 True 停止。"""
    calls: list[int] = []
    stop = asyncio.Event()
    wait_calls = {"n": 0}

    class FakeLtm:
        async def hard_purge_soft_deleted(self, **kwargs) -> int:
            calls.append(1)
            return 2

    async def wait_fn(event: asyncio.Event, timeout: float) -> bool:
        wait_calls["n"] += 1
        if wait_calls["n"] == 1:
            return False  # 超时 → 进入 purge
        return True  # 停止

    cfg = LTMPurgeConfig(
        enabled=True,
        interval_seconds=60,
        retention_seconds=10,
        batch_limit=10,
    )

    async def _run() -> None:
        await run_ltm_hard_purge_loop(
            get_ltm=lambda: FakeLtm(),
            purge_config=cfg,
            stop_event=stop,
            wait_fn=wait_fn,
        )

    asyncio.run(_run())
    assert calls == [1]
    assert wait_calls["n"] == 2
