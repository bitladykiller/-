"""
记忆模块配置文件。

STM = Short-Term Memory，短期记忆。
LTM = Long-Term Memory，长期记忆。

本模块定义短期记忆和长期记忆的配置参数。
"""

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
        # Prompt 中最多使用最近 8 轮对话
        "max_rounds": 8,
        
        # 一轮通常包含 user + assistant，所以 8 轮约等于 16 条消息
        "max_messages": 16,
    },

    # 压缩配置
    "compression": {
        # 是否启用压缩
        "enabled": True,
        
        # 距离上次压缩超过 6 轮后触发压缩
        "trigger_rounds": 6,
        
        # Redis List 中消息数量超过 20 条后触发压缩
        "trigger_messages": 20,
        
        # 压缩时仍保留最近 4 轮原始消息
        "keep_recent_rounds": 4,
        
        # 压缩摘要最多约 2000 个字符
        "summary_max_chars": 2000,
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
# 长期记忆类型（v3.2: user_profile 迁移到 MySQL + Redis 缓存）
LONG_TERM_MEMORY_TYPES = {
    "ISSUE_HISTORY": "issue_history",    # 历史问题 → Milvus 语义检索
    "SOLUTION_NOTE": "solution_note",    # 有效方案 → Milvus 语义检索
    # USER_PROFILE 已移除 —— 结构化字段存入 MySQL user_profiles + user_facts 表
    # Redis 缓存 TTL=30min，查询优先读缓存
}

# ------------------------------------------------------------------ #
# 敏感信息过滤规则
# ------------------------------------------------------------------ #

# 不允许写入长期记忆的敏感信息模式
SENSITIVE_PATTERNS = [
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
]