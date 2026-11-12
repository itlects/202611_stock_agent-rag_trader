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

-- v0.2: source별 키로 CSV가 실시간 캐시를 덮어쓰는 것을 방지한다.
CREATE TABLE IF NOT EXISTS market_quotes (
 symbol CHAR(6) NOT NULL,
 source VARCHAR(40) NOT NULL,
 payload JSON NOT NULL,
 updated_at TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 PRIMARY KEY(symbol, source)
) ENGINE=InnoDB;
