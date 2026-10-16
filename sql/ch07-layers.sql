SET NAMES utf8mb4;
SET @ch07_ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='conversations' AND column_name='layer1_from_msg_id')=0, 'ALTER TABLE conversations ADD COLUMN layer1_from_msg_id BIGINT NULL', 'SELECT 1');
PREPARE ch07_stmt FROM @ch07_ddl;
EXECUTE ch07_stmt;
DEALLOCATE PREPARE ch07_stmt;
CREATE TABLE IF NOT EXISTS conversation_summaries (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  conversation_id BIGINT NOT NULL,
  seq INT NOT NULL,
  from_msg_id BIGINT NOT NULL,
  upto_msg_id BIGINT NOT NULL,
  content TEXT NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_conversation_summary_seq (conversation_id, seq),
  CONSTRAINT fk_summary_conversation FOREIGN KEY (conversation_id)
    REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
