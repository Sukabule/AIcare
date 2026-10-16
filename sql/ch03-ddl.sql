SET NAMES utf8mb4;
CREATE TABLE IF NOT EXISTS knowledge_chunks (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 category VARCHAR(255) NOT NULL,
 questions TEXT NOT NULL,
 answer TEXT NOT NULL,
 section_path VARCHAR(512) NULL,
 content_type VARCHAR(32) NULL,
 is_key_clause TINYINT(1) NOT NULL DEFAULT 0,
 prev_chunk_id BIGINT UNSIGNED NULL,
 next_chunk_id BIGINT UNSIGNED NULL,
 vector_id VARCHAR(64) NULL,
 vectorize_status ENUM('pending','done') NOT NULL DEFAULT 'pending',
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 PRIMARY KEY (id), KEY idx_category(category), KEY idx_vectorize_status(vectorize_status),
 CONSTRAINT fk_chunks_prev FOREIGN KEY(prev_chunk_id) REFERENCES knowledge_chunks(id) ON DELETE SET NULL,
 CONSTRAINT fk_chunks_next FOREIGN KEY(next_chunk_id) REFERENCES knowledge_chunks(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识原文权威源';
CREATE TABLE IF NOT EXISTS qa_extraction_staging (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 batch_no VARCHAR(64) NOT NULL,
 source_ref VARCHAR(255) NULL,
 question TEXT NOT NULL,
 answer TEXT NOT NULL,
 status ENUM('extracted','kept','discarded') NOT NULL DEFAULT 'extracted',
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(id), KEY idx_batch_no(batch_no), KEY idx_status(status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话抽取暂存';
