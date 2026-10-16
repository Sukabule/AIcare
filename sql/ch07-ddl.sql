SET NAMES utf8mb4;
SET @ch07_ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='conversations' AND column_name='summary')=0, 'ALTER TABLE conversations ADD COLUMN summary TEXT NULL', 'SELECT 1');
PREPARE ch07_stmt FROM @ch07_ddl;
EXECUTE ch07_stmt;
DEALLOCATE PREPARE ch07_stmt;
SET @ch07_ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='conversations' AND column_name='summary_upto_msg_id')=0, 'ALTER TABLE conversations ADD COLUMN summary_upto_msg_id BIGINT NULL', 'SELECT 1');
PREPARE ch07_stmt FROM @ch07_ddl;
EXECUTE ch07_stmt;
DEALLOCATE PREPARE ch07_stmt;
