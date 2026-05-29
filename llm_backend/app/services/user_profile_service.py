"""
用户画像服务 — MySQL 存储 + Redis 缓存。

v3.2: 结构化画像从 Milvus 迁移到 MySQL。
Redis 缓存 TTL = 30 分钟，减少 MySQL 查询。
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from app.core.database import AsyncSessionLocal


class UserProfileService:
    """用户画像 CRUD + Redis 缓存。"""

    CACHE_TTL = 1800  # 30 分钟
    CACHE_PREFIX = "user:profile"

    # ------------------------------------------------------------------ #
    # 画像读写
    # ------------------------------------------------------------------ #

    @staticmethod
    async def get_profile(user_id: int, redis_client=None) -> Dict[str, Any]:
        """获取用户画像。

        优先读 Redis 缓存（TTL 30min），未命中查 MySQL。
        """
        # 缓存命中
        if redis_client:
            cached = await redis_client.get(
                f"{UserProfileService.CACHE_PREFIX}:{user_id}"
            )
            if cached:
                return json.loads(cached)

        # 查 MySQL
        profile = {"user_id": user_id, "preferred_brand": None, "budget_range": None,
                   "preferred_category": None, "tags": [], "facts": []}

        try:
            async with AsyncSessionLocal() as db:
                # user_profiles 表
                row = await db.execute(
                    text("SELECT * FROM user_profiles WHERE user_id = :uid"),
                    {"uid": user_id}
                )
                row = row.first()
                if row:
                    profile.update({
                        "preferred_brand": row[1] if len(row) > 1 else None,
                        "budget_range": row[2] if len(row) > 2 else None,
                        "preferred_category": row[3] if len(row) > 3 else None,
                        "tags": json.loads(row[4]) if len(row) > 4 and row[4] else [],
                    })

                # user_facts 表（只取活跃版本）
                facts = await db.execute(
                    text("SELECT fact_key, fact_value FROM user_facts "
                         "WHERE user_id = :uid AND is_active = TRUE"),
                    {"uid": user_id}
                )
                profile["facts"] = [
                    {"key": r[0], "value": r[1]} for r in facts.fetchall()
                ]

            # 写缓存
            if redis_client:
                await redis_client.setex(
                    f"{UserProfileService.CACHE_PREFIX}:{user_id}",
                    UserProfileService.CACHE_TTL,
                    json.dumps(profile, ensure_ascii=False),
                )
        except Exception:
            pass

        return profile

    @staticmethod
    async def upsert_profile(
        user_id: int,
        preferred_brand: Optional[str] = None,
        budget_range: Optional[str] = None,
        preferred_category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        redis_client=None,
    ):
        """更新用户画像。只更新非空字段。"""
        sets = []
        params = {"uid": user_id}
        if preferred_brand:
            sets.append("preferred_brand = :brand"); params["brand"] = preferred_brand
        if budget_range:
            sets.append("budget_range = :budget"); params["budget"] = budget_range
        if preferred_category:
            sets.append("preferred_category = :cat"); params["cat"] = preferred_category
        if tags is not None:
            sets.append("tags = :tags"); params["tags"] = json.dumps(tags, ensure_ascii=False)
        if not sets:
            return True

        try:
            async with AsyncSessionLocal() as db:
                sql = (
                    f"INSERT INTO user_profiles (user_id, {', '.join(s.split('=')[0].strip() for s in sets)}) "
                    f"VALUES (:uid, {', '.join(':' + s.split('=')[0].strip().split()[0] for s in sets)}) "
                    f"ON DUPLICATE KEY UPDATE {', '.join(sets)}"
                )
                await db.execute(text(sql), params)
                await db.commit()

            # 清除缓存
            if redis_client:
                await redis_client.delete(f"{UserProfileService.CACHE_PREFIX}:{user_id}")
            return True
        except Exception:
            return False

    @staticmethod
    async def upsert_fact(
        user_id: int,
        fact_key: str,
        fact_value: str,
        redis_client=None,
    ) -> bool:
        """更新用户事实（key-value）。

        存在且值不同 → 版本 +1，旧版本标记 inactive。
        不存在 → 新建。
        值相同 → 不操作。
        """
        try:
            async with AsyncSessionLocal() as db:
                # 查当前活跃版本
                row = await db.execute(
                    text("SELECT id, fact_value, version FROM user_facts "
                         "WHERE user_id = :uid AND fact_key = :key AND is_active = TRUE"),
                    {"uid": user_id, "key": fact_key}
                )
                row = row.first()

                if row:
                    old_id, old_value, old_version = row[0], row[1], row[2]
                    if old_value == fact_value:
                        return True  # 值相同，不更新

                    # 旧版本标记 inactive
                    await db.execute(
                        text("UPDATE user_facts SET is_active = FALSE, "
                             "superseded_by = NULL WHERE id = :id"),
                        {"id": old_id}
                    )
                    # 新版本（先插入再关联）
                    await db.execute(
                        text("INSERT INTO user_facts (user_id, fact_key, fact_value, version) "
                             "VALUES (:uid, :key, :val, :ver)"),
                        {"uid": user_id, "key": fact_key, "val": fact_value,
                         "ver": old_version + 1}
                    )
                    new_id = (await db.execute(text("SELECT LAST_INSERT_ID()"))).scalar()
                    await db.execute(
                        text("UPDATE user_facts SET superseded_by = :new_id WHERE id = :old_id"),
                        {"new_id": new_id, "old_id": old_id}
                    )
                else:
                    # 新建
                    await db.execute(
                        text("INSERT INTO user_facts (user_id, fact_key, fact_value) "
                             "VALUES (:uid, :key, :val)"),
                        {"uid": user_id, "key": fact_key, "val": fact_value}
                    )

                await db.commit()

            # 清除缓存
            if redis_client:
                await redis_client.delete(f"{UserProfileService.CACHE_PREFIX}:{user_id}")
            return True
        except Exception:
            return False
