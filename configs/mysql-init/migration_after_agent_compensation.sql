-- v3.36+: after_agent 可补偿机制 + 压缩/画像/命中幂等强化
-- 用法：
--   mysql -u root -p kefu_agent < configs/mysql-init/migration_after_agent_compensation.sql

USE kefu_agent;

-- ================================================================
-- 1. turn_view_status：after_agent 各物化视图独立状态追踪
--    解决：内部吞异常后无法补偿的问题
-- ================================================================
CREATE TABLE IF NOT EXISTS turn_view_status (
    id INT AUTO_INCREMENT PRIMARY KEY,
    turn_id VARCHAR(128) NOT NULL COMMENT '关联 turn_id',
    view_name VARCHAR(32) NOT NULL COMMENT '视图名: history|stm|compression|ltm|profile|hits',
    status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending|completed|failed|skipped',
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_turn_view (turn_id, view_name),
    INDEX idx_turn_view_status (status),
    INDEX idx_turn_id (turn_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='回合各物化视图写入状态，支持失败视图独立补偿';

-- ================================================================
-- 2. compression_tasks：STM 压缩显式幂等键
--    解决：multi-step 压缩过程中崩溃留下半状态
-- ================================================================
CREATE TABLE IF NOT EXISTS compression_tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    compression_id CHAR(64) NOT NULL COMMENT 'SHA256(session_id:from_turn:to_turn)',
    session_id VARCHAR(128) NOT NULL,
    tenant_id VARCHAR(128) NOT NULL DEFAULT '',
    user_id VARCHAR(128) NOT NULL DEFAULT '',
    from_turn INT NOT NULL,
    to_turn INT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'processing' COMMENT 'processing|completed|failed',
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    UNIQUE KEY uk_compression_id (compression_id),
    INDEX idx_compression_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='STM 压缩任务幂等状态，防止状态不一致的重放';

-- ================================================================
-- 3. memory_hit_events：LTM hit_count 去重
--    解决：hit_count += 1 天生非幂等
-- ================================================================
CREATE TABLE IF NOT EXISTS memory_hit_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    turn_id VARCHAR(128) NOT NULL,
    memory_id VARCHAR(128) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_hit_event (turn_id, memory_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='LTM 命中事件去重，首次 INSERT 成功才增加 hit_count';

-- ================================================================
-- 4. user_facts 增加来源追踪 + 事件级唯一条目去重
--    解决：画像"按最终状态幂等"，缺少事件级硬防线
-- ================================================================
ALTER TABLE user_facts
    ADD COLUMN IF NOT EXISTS source_turn_id VARCHAR(128) NULL COMMENT '来源 turn 事件 ID',
    ADD COLUMN IF NOT EXISTS source_memory_id VARCHAR(128) NULL COMMENT '来源语义记忆 ID';

-- 同一次抽取产出的同 key 事实只保留首次写入
CREATE UNIQUE INDEX IF NOT EXISTS uk_user_fact_source
    ON user_facts (user_id, fact_key, source_turn_id);
