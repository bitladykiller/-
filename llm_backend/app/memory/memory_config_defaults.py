"""记忆模块默认静态配置数据。

职责：
- 承接 STM / LTM 的默认配置字典
- 承接长期记忆类型常量和敏感模式常量

边界：
- 不定义 TypedDict 类型
- 不提供 accessor 或缓存 helper
- 不编译正则，也不参与运行时逻辑
"""

from __future__ import annotations

# ------------------------------------------------------------------ #
# 短期记忆配置
# ------------------------------------------------------------------ #

SHORT_TERM_MEMORY_CONFIG = {
    # 是否启用短期记忆
    "enabled": True,
    # Redis 相关配置
    "redis": {
        # Redis Key 前缀，用于隔离不同模块的 Key
        "key_prefix": "agent:stm",
        # 短期记忆默认保留时间，单位秒
        # 默认 86400 秒 = 24 小时
        "ttl_seconds": 86400,
        # 压缩锁过期时间，单位秒
        # 避免死锁，默认 10 秒
        "lock_ttl_seconds": 10,
    },
    # 滑动窗口配置
    "window": {
        # 一轮通常包含 user + assistant，所以 8 轮约等于 16 条消息
        "max_messages": 16,
    },
    # 时间窗口配置
    # 旧代码里默认退回到 ttl_seconds，这里显式写出，避免阅读时猜测实际生效值。
    "time_window_seconds": 86400,
    # 压缩配置
    "compression": {
        # 是否启用压缩
        "enabled": True,
        # 距离上次压缩超过 6 轮后触发压缩
        "trigger_rounds": 6,
        # Redis ZSET 中消息数量超过 20 条后触发压缩
        "trigger_messages": 20,
        # 压缩时仍保留最近 4 轮原始消息
        "keep_recent_rounds": 4,
    },
}

# ------------------------------------------------------------------ #
# 长期记忆配置
# ------------------------------------------------------------------ #

LONG_TERM_MEMORY_CONFIG = {
    # 是否启用长期记忆
    "enabled": True,
    # Milvus Collection 名称
    "collection_name": "customer_agent_long_memory",
    # 检索配置
    "search": {
        # 最多召回 5 条长期记忆
        "top_k": 5,
        # 相似度阈值，低于该分数不注入 Prompt
        # 注意：如果使用 cosine similarity，分数越大越相似
        # 如果使用 L2 distance，需要转换成相似度分数
        "score_threshold": 0.72,
    },
    # 去重配置
    "deduplication": {
        # 去重时检索 top_k
        "top_k": 3,
        # 相似度阈值，高于该值认为已有相似记忆，不新增
        "similarity_threshold": 0.88,
    },
    # 命中更新配置
    "update_on_hit": {
        # 是否启用命中更新
        "enabled": True,
        # 是否更新 last_hit_at
        "update_last_hit_at": True,
        # 是否增加 hit_count
        "increase_hit_count": True,
    },
}

# ------------------------------------------------------------------ #
# 长期记忆类型
# 这里只保留需要走 Milvus 语义检索的非结构化记忆。
# 用户画像已拆到独立画像存储链路，不再作为 LTM 类型参与向量检索。
# ------------------------------------------------------------------ #

LONG_TERM_MEMORY_TYPES = {
    "ISSUE_HISTORY": "issue_history",  # 历史问题 → Milvus 语义检索
    "SOLUTION_NOTE": "solution_note",  # 有效方案 → Milvus 语义检索
}

# ------------------------------------------------------------------ #
# 敏感信息过滤规则
# ------------------------------------------------------------------ #

# 不允许写入长期记忆的敏感信息模式。
# 使用 tuple 而不是 list，强调这是一组常量规则，而不是运行时可变配置。
SENSITIVE_PATTERNS: tuple[str, ...] = (
    # 密码
    r"password|密码|passwd",
    # 验证码
    r"验证码|verification.code|captcha",
    # 完整身份证号（18位）
    r"\d{17}[\dXx]",
    # 完整银行卡号（16-19位）
    r"\d{16,19}",
    # Token、密钥、access key
    r"token|secret|access.key|api.key",
    # 手机号（11位）
    r"1[3-9]\d{9}",
)

__all__ = [
    "LONG_TERM_MEMORY_CONFIG",
    "LONG_TERM_MEMORY_TYPES",
    "SENSITIVE_PATTERNS",
    "SHORT_TERM_MEMORY_CONFIG",
]
