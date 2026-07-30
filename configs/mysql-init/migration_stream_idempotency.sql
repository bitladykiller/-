-- Redis Stream 消费端幂等（Inbox）增量迁移
-- 用法：
--   mysql -u root -p kefu_agent < configs/mysql-init/migration_stream_idempotency.sql

USE kefu_agent;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS turn_event_id VARCHAR(128) NULL
    COMMENT 'Redis Stream turn_completed 幂等事件 ID' AFTER message_type;

CREATE UNIQUE INDEX IF NOT EXISTS uk_messages_turn_event_sender
    ON messages (conversation_id, turn_event_id, sender);

CREATE TABLE IF NOT EXISTS processed_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_type VARCHAR(128) NOT NULL,
    event_id VARCHAR(128) NOT NULL,
    stream_name VARCHAR(128) NOT NULL DEFAULT '',
    stream_entry_id VARCHAR(64) NOT NULL DEFAULT '',
    payload_hash CHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'processing',
    attempts INT NOT NULL DEFAULT 0,
    lease_owner VARCHAR(128) NOT NULL DEFAULT '',
    lease_expires_at DATETIME NULL,
    last_error TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    dead_lettered_at DATETIME NULL,
    UNIQUE KEY uk_processed_event (event_type, event_id),
    INDEX idx_processed_events_status (status),
    INDEX idx_processed_events_type (event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Redis Stream 消费端幂等收件箱';
