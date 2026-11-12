CREATE DATABASE IF NOT EXISTS agent_rag_trader CHARACTER SET utf8mb4;
USE agent_rag_trader;
CREATE TABLE IF NOT EXISTS runs (
 id CHAR(36) PRIMARY KEY,
 mode VARCHAR(20) NOT NULL,
 state VARCHAR(20) NOT NULL,
 payload JSON NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
-- DBA 계정에서 별도 실행: 비밀번호는 직접 변경한다.
-- CREATE USER 'trader'@'localhost' IDENTIFIED BY '개별적으로_설정한_비밀번호';
-- GRANT SELECT, INSERT, UPDATE ON agent_rag_trader.* TO 'trader'@'localhost';
