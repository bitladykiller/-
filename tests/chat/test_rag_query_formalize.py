"""RAG 书面化改写单元测试。"""

from __future__ import annotations

import asyncio

from app.chat.infrastructure.retrievers.rag_query_formalize import (
    FormalizedQuery,
    formalize_rag_query,
    light_normalize_query,
)
from app.shared.core.app_config import RagRewriteConfig


def test_light_normalize_strips_fillers() -> None:
    assert "门锁连不上网" in light_normalize_query("亲～门锁连不上网啊")
    assert light_normalize_query("  ") == ""


def test_formalize_disabled_returns_original() -> None:
    cfg = RagRewriteConfig(formalize_enabled=False)

    async def _run() -> str:
        return await formalize_rag_query("亲～能退货吗", config=cfg)

    assert asyncio.run(_run()) == "亲～能退货吗"


def test_formalize_uses_structured_invoke() -> None:
    cfg = RagRewriteConfig(formalize_enabled=True, timeout_seconds=2.0)

    async def fake_invoke(question: str) -> FormalizedQuery:
        assert "退" in question or question
        return FormalizedQuery(rewritten="商品退换货政策如何规定")

    async def _run() -> str:
        return await formalize_rag_query(
            "亲这个能退吗",
            config=cfg,
            structured_invoke=fake_invoke,
        )

    assert asyncio.run(_run()) == "商品退换货政策如何规定"


def test_formalize_timeout_falls_back_to_light() -> None:
    cfg = RagRewriteConfig(formalize_enabled=True, timeout_seconds=0.05)

    async def slow_invoke(question: str) -> FormalizedQuery:
        await asyncio.sleep(1.0)
        return FormalizedQuery(rewritten="不应出现")

    async def _run() -> str:
        return await formalize_rag_query(
            "亲～保修多久",
            config=cfg,
            structured_invoke=slow_invoke,
        )

    out = asyncio.run(_run())
    assert "保修" in out
    assert out != "不应出现"
