-- Preserve all previously allowed labels, including Ch02's '其他'. No rows are deleted.
SET NAMES utf8mb4;
ALTER TABLE tickets MODIFY COLUMN ticket_type ENUM('售后','投诉','咨询','其他','退款') NOT NULL;
