SET NAMES utf8mb4;
CREATE TABLE IF NOT EXISTS low_confidence_questions (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 conversation_id BIGINT NOT NULL,
 raw_question TEXT NOT NULL,
 source ENUM('retrieval_low_conf','self_check','user_feedback') NOT NULL,
 reason TEXT NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT fk_low_conf_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS faith_cases (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 eval_id VARCHAR(64) NOT NULL UNIQUE,
 bucket VARCHAR(64) NOT NULL,
 query TEXT NOT NULL,
 strategy VARCHAR(32) NOT NULL,
 answer TEXT NOT NULL,
 reason TEXT NOT NULL,
 citations JSON NOT NULL,
 judge_model VARCHAR(128) NOT NULL,
 status ENUM('未解决','已解决','无需解决') NOT NULL DEFAULT '未解决',
 resolution TEXT NULL,
 seen_count INT NOT NULL DEFAULT 1,
 first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 resolved_at DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
