-- Authoritative audit schema. No FK: failed, denied and unknown calls must remain traceable.
SET NAMES utf8mb4;
CREATE TABLE IF NOT EXISTS tool_audit_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  conversation_id BIGINT NOT NULL,
  tool_call_id VARCHAR(255) NOT NULL,
  tool_name VARCHAR(128) NOT NULL,
  tool_source VARCHAR(32) NOT NULL,
  mcp_server VARCHAR(128) NULL,
  arguments JSON NOT NULL,
  result_summary VARCHAR(500) NOT NULL,
  status ENUM('成功','失败','超时','校验拦下','权限拒绝') NOT NULL,
  error_message TEXT NULL,
  retry_count INT UNSIGNED NOT NULL DEFAULT 0,
  duration_ms BIGINT UNSIGNED NOT NULL DEFAULT 0,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY idx_tool_audit_conversation (conversation_id, id),
  KEY idx_tool_audit_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工具执行审计，无外键，失败不影响工具执行';
