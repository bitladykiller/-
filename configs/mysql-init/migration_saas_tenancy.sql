-- SaaS 多租户迁移：身份模型 + 全表 tenant_id 化
-- 用法：
--   mysql -u root -p kefu_agent < configs/mysql-init/migration_saas_tenancy.sql
-- 注意：本迁移假定已按顺序执行过此前各 migration；请在执行前备份数据库。

USE kefu_agent;

-- ================================================================
-- 1. 身份模型：tenants + tenant_memberships
-- ================================================================

CREATE TABLE IF NOT EXISTS tenants (
    id VARCHAR(64) PRIMARY KEY COMMENT '租户 ID（default / t_xxx）',
    name VARCHAR(100) NOT NULL COMMENT '租户展示名',
    status VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT 'active|disabled',
    plan VARCHAR(32) NOT NULL DEFAULT 'free' COMMENT 'free|pro|enterprise',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='租户主体：SaaS 数据隔离的一级边界';

CREATE TABLE IF NOT EXISTS tenant_memberships (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    user_id INT NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'member' COMMENT 'owner|admin|member|viewer',
    status VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT 'active|suspended',
    joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_membership (tenant_id, user_id),
    INDEX idx_membership_user (user_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='用户-租户多对多成员关系';

-- 存量账号全部归入 default 租户（每个用户一条 owner 关系）
INSERT INTO tenants (id, name, status, plan)
SELECT 'default', '默认租户', 'active', 'free'
WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE id = 'default');

INSERT IGNORE INTO tenant_memberships (tenant_id, user_id, role, status)
SELECT 'default', id, 'owner', 'active' FROM users;

-- ================================================================
-- 2. conversations：加 tenant_id + 复合索引
-- ================================================================
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'
        COMMENT '租户边界（SaaS 一级隔离维度）' AFTER id;

CREATE INDEX IF NOT EXISTS idx_conversation_tenant_user
    ON conversations (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_conversation_tenant_id
    ON conversations (tenant_id, id);

-- ================================================================
-- 3. user_profiles：主键从 user_id 升级为 (tenant_id, user_id)
-- ================================================================
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'
        COMMENT '租户边界' FIRST,
    DROP PRIMARY KEY,
    ADD PRIMARY KEY (tenant_id, user_id);

-- ================================================================
-- 4. user_facts：tenant_id + 租户内唯一键（先删旧键，再建新键）
-- ================================================================
ALTER TABLE user_facts
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'
        COMMENT '租户边界' AFTER id;

ALTER TABLE user_facts
    DROP INDEX uk_user_fact_active,
    DROP INDEX uk_user_fact_source;

ALTER TABLE user_facts
    ADD UNIQUE KEY uk_user_fact_active (tenant_id, user_id, active_fact_key),
    ADD UNIQUE KEY uk_user_fact_source (tenant_id, user_id, fact_key, source_turn_id);

-- ================================================================
-- 5. user_documents：tenant_id + doc_id 租户内唯一
-- ================================================================
ALTER TABLE user_documents
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'
        COMMENT '租户边界' AFTER id;

ALTER TABLE user_documents
    DROP INDEX uk_doc_id;

CREATE UNIQUE INDEX IF NOT EXISTS uk_doc_id
    ON user_documents (tenant_id, doc_id);

CREATE INDEX IF NOT EXISTS idx_user_documents_tenant_user
    ON user_documents (tenant_id, user_id);

-- ================================================================
-- 6. turn_view_status：tenant_id + 租户内唯一键
-- ================================================================
ALTER TABLE turn_view_status
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'
        COMMENT '租户边界' AFTER id;

ALTER TABLE turn_view_status
    DROP INDEX uk_turn_view;

ALTER TABLE turn_view_status
    ADD UNIQUE KEY uk_turn_view (tenant_id, turn_id, view_name);

-- ================================================================
-- 7. memory_hit_events：tenant_id + 租户内唯一键
-- ================================================================
ALTER TABLE memory_hit_events
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'
        COMMENT '租户边界' AFTER id;

ALTER TABLE memory_hit_events
    DROP INDEX uk_hit_event;

ALTER TABLE memory_hit_events
    ADD UNIQUE KEY uk_hit_event (tenant_id, turn_id, memory_id);

-- ================================================================
-- 8. processed_events：tenant_id + 租户内唯一幂等键
-- ================================================================
ALTER TABLE processed_events
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'
        COMMENT '租户边界' AFTER id;

ALTER TABLE processed_events
    DROP INDEX uk_processed_event;

ALTER TABLE processed_events
    ADD UNIQUE KEY uk_processed_event (tenant_id, event_type, event_id);

CREATE INDEX IF NOT EXISTS idx_processed_events_tenant
    ON processed_events (tenant_id, status);

-- ================================================================
-- 9. compression_tasks：对齐默认租户值（原为 ''）
-- ================================================================
UPDATE compression_tasks SET tenant_id = 'default' WHERE tenant_id = '';
