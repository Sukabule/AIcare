-- User-authoritative schema, guards make the same migration safe to rerun.
SET NAMES utf8mb4;
CREATE TABLE IF NOT EXISTS review_queue (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '缺口主键',
 normalized_question VARCHAR(512) NOT NULL COMMENT '标准化后的 FAQ 式问题',
 ai_suggested_answer TEXT NULL COMMENT '模型示例答案，不能直接当政策',
 occurrence_count INT UNSIGNED NOT NULL DEFAULT 1,
 review_status ENUM('待审','通过','驳回') NOT NULL DEFAULT '待审',
 approved_answer TEXT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 KEY idx_review_status (review_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='飞轮待审队列';
CREATE TABLE IF NOT EXISTS eval_runs (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 triggered_by ENUM('定时','手动') NOT NULL DEFAULT '定时',
 dataset_size INT UNSIGNED NOT NULL,
 metrics JSON NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='自动化评估流水线轮次结果';
SET @ch09_sql = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='low_confidence_questions' AND column_name='retrieved_chunks')=0, 'ALTER TABLE low_confidence_questions ADD COLUMN retrieved_chunks JSON NULL COMMENT ''落池时的召回片段快照'' AFTER reason', 'SELECT 1');
PREPARE ch09_stmt FROM @ch09_sql;
EXECUTE ch09_stmt;
DEALLOCATE PREPARE ch09_stmt;
SET @ch09_sql = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='low_confidence_questions' AND column_name='matched_review_id')=0, 'ALTER TABLE low_confidence_questions ADD COLUMN matched_review_id BIGINT UNSIGNED NULL COMMENT ''归并的知识缺口'' AFTER retrieved_chunks', 'SELECT 1');
PREPARE ch09_stmt FROM @ch09_sql;
EXECUTE ch09_stmt;
DEALLOCATE PREPARE ch09_stmt;
SET @ch09_sql = IF((SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='low_confidence_questions' AND index_name='idx_matched_review_id')=0, 'ALTER TABLE low_confidence_questions ADD KEY idx_matched_review_id (matched_review_id)', 'SELECT 1');
PREPARE ch09_stmt FROM @ch09_sql;
EXECUTE ch09_stmt;
DEALLOCATE PREPARE ch09_stmt;
SET @ch09_sql = IF((SELECT COUNT(*) FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='low_confidence_questions' AND constraint_name='fk_lcq_review')=0, 'ALTER TABLE low_confidence_questions ADD CONSTRAINT fk_lcq_review FOREIGN KEY (matched_review_id) REFERENCES review_queue(id) ON DELETE SET NULL', 'SELECT 1');
PREPARE ch09_stmt FROM @ch09_sql;
EXECUTE ch09_stmt;
DEALLOCATE PREPARE ch09_stmt;
