-- 创建数据库（如果不存在）
CREATE DATABASE IF NOT EXISTS kefu_agent DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE kefu_agent;

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_login DATETIME NULL,
    status VARCHAR(20) DEFAULT 'active',
    INDEX idx_username (username),
    INDEX idx_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 对话表（v3.2: 废弃，由 Redis STM + Milvus LTM 替代）
CREATE TABLE IF NOT EXISTS conversations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    title VARCHAR(100) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    status VARCHAR(20) DEFAULT 'ongoing',
    dialogue_type ENUM('NORMAL', 'DEEP_THINKING', 'WEB_SEARCH', 'RAG') NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_user_id (user_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 消息表
CREATE TABLE IF NOT EXISTS messages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    conversation_id INT NOT NULL,
    sender VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    message_type VARCHAR(20) DEFAULT 'text',
    turn_event_id VARCHAR(128) NULL COMMENT 'Redis Stream turn_completed 幂等事件 ID',
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    INDEX idx_conversation_id (conversation_id),
    INDEX idx_sender (sender),
    UNIQUE KEY uk_messages_turn_event_sender (conversation_id, turn_event_id, sender)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 插入示例用户数据（演示密码统一为 demo1234，bcrypt 哈希；生产部署请删除种子用户）
INSERT INTO users (username, email, password_hash, status) VALUES
('admin', 'admin@example.com', '$2b$12$cSfGnR7kFyCMvPN3XJsG5u4H2p5HQeRn6Up44MIEvz8rAkZN94QUu', 'active'),
('test_user', 'test@example.com', '$2b$12$cSfGnR7kFyCMvPN3XJsG5u4H2p5HQeRn6Up44MIEvz8rAkZN94QUu', 'active'),
('demo_user', 'demo@example.com', '$2b$12$cSfGnR7kFyCMvPN3XJsG5u4H2p5HQeRn6Up44MIEvz8rAkZN94QUu', 'active');

-- 插入示例对话数据
INSERT INTO conversations (user_id, title, dialogue_type) VALUES
(1, '智能客服体验对话', 'NORMAL'),
(1, '产品咨询', 'RAG'),
(2, '技术支持', 'DEEP_THINKING'),
(2, '订单查询', 'WEB_SEARCH'),
(3, '产品推荐', 'NORMAL');

-- 插入示例消息数据
INSERT INTO messages (conversation_id, sender, content, message_type) VALUES
(1, 'user', '你好，我想了解一下智能门铃的功能', 'text'),
(1, 'assistant', '您好！智能门铃是智能家居的重要组成部分，主要功能包括远程监控、人脸识别、双向语音通话等。您想了解哪款产品呢？', 'text'),
(1, 'user', '谷歌智能门铃 Basic 怎么样？', 'text'),
(1, 'assistant', '谷歌智能门铃 Basic 是一款性价比很高的产品，售价3322.66元，目前库存充足。它支持高清视频监控、移动侦测、云端存储等功能。', 'text'),
(2, 'user', '帮我查询一下智能冰箱的库存情况', 'text'),
(2, 'assistant', '好的，我来为您查询智能冰箱的库存信息...', 'text'),
(3, 'user', '我遇到了连接问题，设备无法联网', 'text'),
(3, 'assistant', '我理解您遇到的问题。让我帮您分析一下可能的原因和解决方案...', 'text'),
(4, 'user', '查询订单号1234567的物流状态', 'text'),
(4, 'assistant', '正在为您查询订单物流信息...', 'text'),
(5, 'user', '有什么好的智能音箱推荐吗？', 'text'),
(5, 'assistant', '根据您的需求，我为您推荐以下几款智能音箱...', 'text');

-- ============================================================ --
-- v3.2: 用户画像表（替代 Milvus 中的 user_profile 类型）
-- ============================================================ --

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id INT PRIMARY KEY,
    preferred_brand VARCHAR(64) DEFAULT NULL COMMENT '偏好品牌：google/apple/xiaomi/huawei',
    budget_range VARCHAR(32) DEFAULT NULL COMMENT '预算范围：0-1000/1000-3000/3000-5000/5000+',
    preferred_category VARCHAR(128) DEFAULT NULL COMMENT '偏好品类：智能门铃/智能音箱/智能照明',
    tags JSON DEFAULT NULL COMMENT '多值标签：["smart_home","price_sensitive","early_adopter"]',
    language VARCHAR(16) DEFAULT 'zh-CN',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='用户画像（结构化字段，精确查询，统计聚合）';

CREATE TABLE IF NOT EXISTS user_facts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    fact_key VARCHAR(128) NOT NULL COMMENT '事实键：workplace/family_size/pet/expertise',
    fact_value VARCHAR(256) NOT NULL COMMENT '事实值：ali/3/cat/backend',
    version INT DEFAULT 1 COMMENT '版本号（冲突更新 +1）',
    is_active BOOLEAN DEFAULT TRUE COMMENT '当前有效版本',
    superseded_by INT DEFAULT NULL COMMENT '被哪个 id 替代',
    source_turn_id VARCHAR(128) NULL COMMENT '来源 turn 事件 ID',
    source_memory_id VARCHAR(128) NULL COMMENT '来源语义记忆 ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_fact (user_id, fact_key),
    UNIQUE KEY uk_user_fact_source (user_id, fact_key, source_turn_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='用户事实（key-value，支持版本追踪、来源追溯和事件级幂等）';

-- 示例用户画像数据
INSERT INTO user_profiles (user_id, preferred_brand, budget_range, preferred_category, tags) VALUES
(1, 'google', '3000-5000', '智能门铃,智能音箱', '["smart_home","early_adopter"]'),
(2, 'xiaomi', '1000-3000', '智能照明,智能安防', '["price_sensitive","smart_home"]'),
(3, 'apple', '5000+', '智能音箱,智能厨电', '["premium_user","early_adopter"]');

INSERT INTO user_facts (user_id, fact_key, fact_value, version) VALUES
(1, 'workplace', 'ali', 1),
(1, 'family_size', '3', 1),
(2, 'workplace', 'tencent', 1),
(3, 'pet', 'cat', 1);

-- ============================================================ --
-- v3.30+: 用户知识文档元信息（Milvus 向量仍按 doc_id 存；本表供列表/更新绑定）
-- ============================================================ --

CREATE TABLE IF NOT EXISTS user_documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    doc_id VARCHAR(64) NOT NULL COMMENT '与 Milvus chunk.doc_id 一致，全局唯一',
    title VARCHAR(255) NOT NULL COMMENT '前端展示名',
    original_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '最近一次上传原始文件名',
    source_path VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '服务器落盘路径',
    content_hash VARCHAR(64) NOT NULL DEFAULT '' COMMENT '内容 SHA256',
    status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending|indexing|ready|failed',
    version INT NOT NULL DEFAULT 0 COMMENT '与 Milvus chunk.version 对齐',
    chunk_count INT NOT NULL DEFAULT 0,
    last_task_id VARCHAR(64) NOT NULL DEFAULT '',
    error_message TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_doc_id (doc_id),
    INDEX idx_user_documents_user (user_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='用户 RAG 文档元信息';

-- ============================================================ --
-- v3.36+: after_agent 可补偿机制 + 压缩/画像/命中幂等强化
-- ============================================================ --

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
COMMENT='STM 压缩任务幂等状态';

CREATE TABLE IF NOT EXISTS memory_hit_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    turn_id VARCHAR(128) NOT NULL,
    memory_id VARCHAR(128) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_hit_event (turn_id, memory_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='LTM 命中事件去重';

-- ============================================================ --
-- Redis Streams 消费端 Inbox：至少一次投递下的业务幂等控制
-- ============================================================ --

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
