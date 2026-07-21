-- 已有环境增量迁移：用户知识文档元信息表
-- 用法示例：
--   mysql -u root -p kefu_agent < configs/mysql-init/migration_user_documents.sql

USE kefu_agent;

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
