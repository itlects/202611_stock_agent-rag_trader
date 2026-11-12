# 국내주식 Agent·RAG 자동매매 개발 튜토리얼

작성 기준: 2026-11-13 / 초기 구현 v0.1

## 1. 목표와 현재 구현 범위

이 프로젝트는 퀀트 지표로 후보를 고르고 문서를 검색한 근거와 함께 분석한 뒤, 코드로 작성한 한도 검사를 통과한 주문만 처리하는 구조를 학습한다. 수익 극대화는 목표이지만 보장되는 결과가 아니다.

현재 구현은 Python CLI, 로컬 문서 검색, 선택적 OpenAI 분석, 승인형/자동형 로컬 모의체결, MySQL 기록이다. 자율적으로 도구를 선택하는 LLM Agent가 아니라 역할을 분리한 고정 순서의 Agent 워크플로다. RAG는 문자 bigram 검색을 사용하는 최소 구현이며 벡터 임베딩을 사용하지 않는다. 문서 출처·청크 번호·검색 점수를 결과에 남긴다.

기본 데모는 합성 가격 두 건을 사용한다. v0.2는 키움 인증·단일/일괄 스냅샷·WebSocket 시세 수집 및 MySQL 캐시 연결 코드를 추가했다. 실제 서버 통합검증, 실계좌 주문, 수익률 백테스트, 웹 대시보드는 아직 완료하지 않았다. 로컬 모의체결은 증권사의 체결 결과가 아니다.

## 2. 사용할 기술

| 구성 | 기술과 목적 |
|---|---|
| 개발 언어 | Python 3.11 이상, 표준 라이브러리 중심 |
| 데이터베이스 | MySQL Community Server 8.0.46 / InnoDB / utf8mb4 |
| DB 드라이버 | mysql-connector-python 9.1.0, 프로젝트 재현용 고정 버전 |
| RAG | Markdown 수집 → 800자 청크, 200자 겹침 → bigram 검색 → 상위 3개 |
| 분석 | 기본 규칙 기반 설명, 선택적 OpenAI Responses API |
| 주문 | Python 한도 검사와 로컬 모의체결 |
| 품질 확인 | unittest와 GitHub Actions |

## 3. 구성요소와 실행 순서

Research 역할의 retrieve()는 문서를 가져온다. Analyst 역할의 explain()은 검색 결과를 설명한다. Portfolio 역할의 recommend()는 양의 모멘텀 종목을 정렬하고 각 1주를 선택한다. Risk 역할의 risk_check()는 수량·가격·예산을 검사한다. Execution 역할의 fill()은 모의체결 상태로 바꾼다. Store는 추천과 처리 결과를 MySQL에 저장한다.

LLM 출력은 설명으로만 저장되며 종목·수량·가격을 변경하지 않는다. 검색된 문서 내용이 코드 실행이나 주문 도구 호출로 이어지는 경로도 없다.

## 4. Windows 개발환경 만들기

1. Python 3.11 이상, Git, VS Code를 설치한다. PowerShell에서 다음을 확인한다.

```powershell
py --version
git --version
```

2. 프로젝트를 내려받고 가상환경을 만든다.

```powershell
git clone https://github.com/itlects/202611_stock_agent-rag_trader.git
cd 202611_stock_agent-rag_trader
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Python 3.11이 없으면 설치한 버전에 맞춰 `py -3.12` 등으로 바꾼다. 가상환경 활성화 없이 실행하므로 PowerShell 실행정책 변경이 필요 없다.

3. DB 없이 먼저 확인한다.

```powershell
.\.venv\Scripts\python.exe trader.py demo --mode approval
.\.venv\Scripts\python.exe trader.py demo --mode auto
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

승인 모드의 state는 pending, 자동 모드는 paper_filled다. 자동 모드 총액은 합성 가격 70,000원 + 180,000원 = 250,000원이다. demo 결과는 저장하지 않는다. 화면의 UUID는 매 실행마다 달라진다.

## 5. MySQL 8.0.46 설치와 초기화

MySQL Community Server 8.0.46을 설치하고 서버 서비스를 시작한다. Workbench 8에서 관리자 연결을 만든다. `SELECT VERSION();`으로 버전을 확인하고 `schema.sql` 전체를 SQL 편집기에서 실행한다. 데이터베이스와 runs 테이블이 생성되어야 한다.

관리자 연결에서 로컬 개발용 사용자를 별도로 만든다. 아래 비밀번호는 자신의 값으로 변경한다.

```sql
CREATE USER 'trader'@'localhost' IDENTIFIED BY 'CHANGE_TO_A_UNIQUE_PASSWORD';
GRANT SELECT, INSERT, UPDATE ON agent_rag_trader.* TO 'trader'@'localhost';
```

이미 같은 사용자가 있으면 CREATE USER를 반복하지 않는다. 서버와 클라이언트가 다른 컴퓨터라면 접속 출발지에 맞는 MySQL 계정 호스트를 설정하고 방화벽을 해당 IP로 제한한다.

`.env`를 VS Code로 열어 작성한다. 파일은 저장소에 올리지 않는다.

```dotenv
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=agent_rag_trader
DB_USER=trader
DB_PASSWORD=자신이_설정한_비밀번호
OPENAI_API_KEY=
OPENAI_MODEL=
```

.env 파서는 단순 KEY=VALUE 형식이다. 따옴표를 붙이지 않는다. 기존 OS 환경변수가 있으면 그 값이 우선한다.

## 6. 승인 후 매매와 자동매매

승인형 추천을 저장한다.

```powershell
.\.venv\Scripts\python.exe trader.py run --mode approval
```

출력된 id, orders, evidence, analysis를 검토한다. 처리할 추천의 실제 id를 다음 명령에 넣는다.

```powershell
.\.venv\Scripts\python.exe trader.py approve 출력된_UUID
```

동일 UUID를 다시 승인하면 오류로 차단한다. MySQL 행 잠금과 pending 상태 확인이 중복 처리를 막는다. 자동형은 다음과 같다.

```powershell
.\.venv\Scripts\python.exe trader.py run --mode auto
```

한 번 실행하면 한 번의 추천 묶음만 처리하고 종료한다. 자동형도 실제 계좌에는 연결되지 않는다. 묶음별 예산 100만원, 개별 주문 30만원이다. 여러 실행에 걸친 계좌 총예산·잔고·보유수량·일손실 한도는 현재 계산하지 않으므로 무한 반복 스케줄러를 붙이지 않는다.

Workbench에서 기록을 확인한다.

```sql
SELECT id, mode, state, created_at FROM agent_rag_trader.runs ORDER BY created_at DESC;
SELECT JSON_PRETTY(payload) FROM agent_rag_trader.runs ORDER BY created_at DESC LIMIT 1;
```

## 7. RAG 문서 추가와 분석 API 설정

`data/knowledge`에 UTF-8 Markdown 파일을 넣는다. 다음 실행 시 다시 읽으므로 별도 색인 명령은 필요 없다. 교육 문서와 투자 전략 메모부터 넣고, 이후 공시와 뉴스에는 원문 URL·발행시각·수집시각·종목코드를 기록한다. 현재 검색기는 날짜 필터를 구현하지 않았으므로 과거 시점 평가에 쓰면 미래 정보 유입 문제가 생길 수 있다.

LLM을 사용하려면 .env에 OPENAI_API_KEY와 본인 계정에서 이용 가능한 OPENAI_MODEL을 설정하고 실행한다.

```powershell
.\.venv\Scripts\python.exe trader.py run --mode approval --llm
```

선택 시 검색된 문서 청크가 API로 전송되며 사용료가 발생할 수 있다. 계좌정보와 API 키를 지식 문서에 넣지 않는다. 모델 응답이 비거나 API가 실패하면 해당 추천 생성을 중단한다. API 인증 실패 시 키와 모델 접근권한을 확인한다. 키가 없을 때는 --llm 없이 실행한다.

## 8. 소스 코드 읽기

| 파일/함수 | 학습 포인트 |
|---|---|
| trader.py / load_env | 환경변수와 비밀정보 분리 |
| retrieve | 문서 청크, 겹침, 검색 근거 |
| explain | 검색 증강 프롬프트와 외부 API 호출 |
| recommend | 후보 정렬과 구조화된 추천 결과 |
| risk_check | LLM과 독립적인 결정적 검증 |
| Store.execute | 트랜잭션, FOR UPDATE, 중복 승인 차단 |
| schema.sql | JSON 기록과 상태 전이 |
| tests/test_trader.py | 승인, 한도, 근거 누락 검사 |

가격 계산은 합성 정수 가격만을 전제로 한다. 실제 주문에서는 Decimal 또는 최소 화폐단위 정수, 거래소 호가단위 검증이 필요하다.

## 9. Ubuntu 개발서버 구성과 실행

Ubuntu에 Python 3.11 이상과 venv, Git을 준비한다. MySQL은 8.0.46 설치본을 사용하고 실제 버전은 SELECT VERSION()으로 확인한다. 배포판 기본 패키지가 항상 8.0.46인 것은 아니다.

```bash
git clone https://github.com/itlects/202611_stock_agent-rag_trader.git
cd 202611_stock_agent-rag_trader
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
.venv/bin/python trader.py demo --mode auto
.venv/bin/python -m unittest discover -s tests -v
```

Workbench 또는 mysql 클라이언트로 schema.sql과 사용자를 설정하고 .env를 수정한다. `mysql -u root -p < schema.sql`은 Linux 셸에서 사용할 수 있다. 이어서 `.venv/bin/python trader.py run --mode approval`로 저장을 확인한다. 현재 제공 실행물은 CLI 배치 프로그램이며 HTTP API나 상주 웹서비스가 아니다. 포트를 열거나 systemd 상시 실행을 구성할 필요가 없다.

## 10. 오류 해결

| 증상 | 확인할 사항 |
|---|---|
| No module named mysql | 현재 가상환경의 pip로 requirements 설치 |
| Access denied | DB_USER/DB_PASSWORD, 사용자 host, 접속권한 |
| Unknown database/table | schema.sql 실행 여부 |
| Can't connect | MySQL 서비스와 DB_HOST/DB_PORT |
| 검색 근거 없음 | knowledge 폴더의 UTF-8 md 파일과 검색어 |
| 이미 처리된 추천 | 새로운 pending UUID인지 확인 |
| 모듈 trader를 못 찾음 | 저장소 루트에서 테스트 명령 실행 |

## 11. 검증 결과와 남은 구현

로컬 Python 3.12에서 핵심 단위 테스트 5개, 승인형 데모, 자동형 데모를 실행했다. MySQL 서버와 사용자 키가 없는 현재 환경에서는 실제 DB 통합, LLM 원격 호출, 키움 연동을 검증하지 못했다. 설치 후 위 SQL 조회와 승인 중복 처리로 DB 통합을 확인한다.

다음 개발 순서는 ① MySQL 통합 테스트와 스키마 버전 관리 ② 키움 모의투자 인증·시세 조회 어댑터 ③ 공시/뉴스 수집 및 시각 필터·임베딩 검색 ④ 거래비용을 포함한 walk-forward 백테스트 ⑤ 잔고·미체결·부분체결·주문 idempotency·킬스위치 ⑥ 인증된 승인 대시보드다. 실거래 주문은 이 검증을 끝낸 뒤 별도 구현한다.

키움은 공식 REST 가이드와 공식 저장소의 최신 API 스펙으로 인증·시세·주문 필드를 확인한다. 키움 모의투자 도메인은 https://mockapi.kiwoom.com 이며 v0.2에는 토큰 발급과 읽기 전용 시세 연결 코드가 있으며 주문 호출은 넣지 않았다. 키를 채운다고 실거래가 활성화되지 않는다.

## 12. 공식 학습 자료

- [키움 REST API 가이드](https://openapi.kiwoom.com/guide/apiguide)
- [키움 공식 예제 저장소](https://github.com/Kiwoom-Securities/Kiwoom-REST-API)
- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Python venv](https://docs.python.org/3/library/venv.html)
- [MySQL 8.0 설명서](https://dev.mysql.com/doc/refman/8.0/en/)
- [MySQL Connector/Python](https://dev.mysql.com/doc/connector-python/en/)
