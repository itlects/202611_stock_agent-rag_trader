# 키움 API 연결과 시세 호출 제한 대응 — v0.2

확인 기준: 2026-11-13, 한국시간. v0.1에 추가되는 읽기 전용 시세 연결 단계다. 키움 REST API 공식 소개와 공식 예제 저장소를 확인했다. 인증키를 설정하면 인증·스냅샷 조회·WebSocket 수집을 실행할 수 있는 코드를 제공한다. 사용자 키가 없어 키움 서버와의 실제 연결 성공을 검증한 상태는 아니다.

## 1. 제한 확인 결과

| 구분 | 공식 안내 | 프로젝트 대응 |
|---|---|---|
| 국내주식 REST 조회 | 계좌별·토큰별 초당 5회; 각 TR 상세 한도도 확인 | 수집기 한 곳에서 최소 0.26초 간격(약 3.85회/초) |
| 국내주식 REST 주문 | 계좌별·토큰별 초당 5회 | 이번 연결은 읽기 전용. 주문 어댑터는 후속 개발 |
| 모의투자 | 계좌별·토큰별 동일 TR 초당 1회 | 보수적으로 모든 요청에 최소 1.05초 간격 |
| WebSocket | 계좌별·토큰별 세션 1개 | MySQL named lock으로 같은 환경 수집기 중복 실행 차단 |
| 실시간 시세 | 세션당 최대 200종목 | 1~200개 KRX 코드 묶음 등록 |
| 실시간 조건검색 | 국내주식 10건, 결과 최대 100종목 | 이번 단계에서 조건검색 미구현 |

출처: [키움 공식 REST API 소개](https://openapi.kiwoom.com/intro?dummyVal=0). 한도는 변경될 수 있으며 운영 직전 각 TR의 상세 가이드와 공지사항을 다시 확인한다. 위 수치는 REST API 기준이다. Windows OCX 방식 OpenAPI+의 분당/시간당 제한을 이 프로젝트에 그대로 섞지 않는다.

“1종목당 1초”가 모든 서비스에 공통 적용되는 것은 아니다. 같은 TR로 20종목을 순차 조회하면 모의투자에서는 약 20초와 통신 시간이 필요할 수 있다. 운영 조회의 공통 한도만 가정하면 이론상 약 4초 이상이며, 본 코드의 보수적 간격으로는 약 5초 이상이다. 다른 프로그램이 같은 계좌/토큰으로 호출한 요청도 고려해야 한다.

WebSocket은 등록 후 서버가 변화를 전송한다. 20종목을 한 패킷으로 구독하므로 20번의 REST 조회를 반복할 필요가 없다. 다만 20종목의 모든 값이 동시에 도착하거나 1초마다 갱신된다는 뜻은 아니다. 거래가 없거나 장이 닫히면 0B 체결 메시지가 오지 않을 수 있다. 초기 화면 표시는 REST 스냅샷으로 보충할 수 있으나 실행용 최신 체결 시세와 구분한다.

추가로 공식 ka10095(지정종목 정보요청)는 여러 종목코드를 `|`로 구분해 받는다. 따라서 “20종목은 무조건 20회 호출”도 아니다. v0.2의 batch-snapshot은 20개씩 묶어 요청한다. 20개는 이 구현의 보수적 묶음 크기이며 공식 최대치라는 뜻이 아니다. 응답에 요청한 종목이 전부 없으면 중단하며 자동 연속조회는 구현하지 않았다. 모의환경 지원 여부와 실제 응답은 사용자 계정에서 확인한다.

## 2. 데이터 수집 구조

한 수집기가 키움에 연결해 실시간 이벤트를 받고 0.5초 단위로 종목별 마지막 값을 MySQL market_quotes에 저장한다. 연구 Agent, RAG 분석, 추천기는 MySQL 캐시를 읽는다. 각 Agent가 별도로 시세 API를 호출하거나 WebSocket 세션을 열지 않는다.

- REST: 토큰 발급과 초기/수동 스냅샷. ka10095로 일괄 초기화하고 이후 0B 스트림으로 갱신한다. HTTP 429·5xx만 제한적으로 재시도하고 서버 Retry-After 숫자값을 반영한다. 업무 오류 코드는 중단한다.
- WebSocket: LOGIN → REG(0B) → REAL. 서버 PING은 그대로 응답하며 일시적 연결 종료 시 최대 3회 재연결하고 다시 등록한다.
- 캐시: 종목과 공급원 복합키로 분리한다. CSV를 가져와도 키움 실시간 값을 덮어쓰지 않는다. 전체 틱 보관이 아니라 최신값 캐시다.
- 신선도: 체결시각과 수신시각을 저장하고 둘 중 하나라도 10초를 넘으면 추천·모의체결을 보류한다. 컴퓨터 시간을 동기화한다. 거래가 드문 종목도 보류될 수 있는 보수적 기본값이다.

본 코드의 제한기는 단일 프로세스 내부에서 공유한다. 동일 DB의 수집기 중복은 잠금으로 막지만, 다른 PC·다른 DB·HTS/API 프로그램의 유량까지 통제하지는 않는다. 실시간 틱 수집기 한 개를 공용으로 사용한다. 200종목을 넘으면 장마감 데이터로 후보를 먼저 추리고 구독 목록을 교체한다. 세션을 여러 개 만들어 제한을 우회하지 않는다.

## 3. API를 사용하지 않는 방법과 한계

| 방법 | 키움 시세 API 사용 | 용도와 현재 구현 |
|---|---|---|
| WebSocket + MySQL 캐시 | 사용함. REST 반복조회는 없음 | 장중 최신 시세 수집, 구현 |
| 정당하게 확보한 CSV/HTS 내보내기 파일 | CSV 입력 시 사용하지 않음 | 종가·과거 데이터 분석, CSV importer 구현 |
| KRX 웹사이트 지연시세 화면 | 프로그램 API 없이 사람이 확인 가능 | 시황 참고. 일부 화면 20분 지연, 자동 수집기 미구현 |
| 금융위 공공데이터 | 키움 API는 안 쓰지만 다른 API는 사용 | 일별 후보 선별/백테스트용. 당일 실시간 대체 아님, 수집 어댑터 미구현 |
| 별도 시세 공급사·코스콤 계약 피드 | 키움 시세 API는 안 씀 | 계약된 실시간 데이터 대안. 공급사 계약·포맷 확정 후 어댑터 개발 |

금융위 주식시세정보의 상세 설명은 일 1회, 기준일 다음 영업일 오후 1시 이후 갱신이라고 명시한다. 같은 페이지의 일반 메타데이터에는 “실시간” 표시가 있어도 당일 체결가로 취급하지 않는다. 출처와 사용·재배포 조건을 확인하고 실제 데이터는 공개 저장소에 넣지 않는다. 예제 CSV는 직접 만든 합성 데이터다.

웹 크롤링은 실시간 보장이나 호출제한 문제를 자동으로 해결하지 않는다. 포털·거래소 화면의 지연 여부와 자동수집 허용 여부를 확인해야 하므로 무단 페이지 스크래핑을 기본 시세 경로로 넣지 않았다. API를 완전히 쓰지 않으면서 최신 체결가를 안정적으로 확보하려면 허가된 실시간 데이터 피드 등의 별도 수신 경로가 필요하다. CSV·지연 자료는 분석에만 사용한다.

## 4. 키움 사용 준비

1. 키움 계좌와 HTS ID를 준비하고 [공식 REST API 사이트](https://openapi.kiwoom.com/)에서 서비스 사용을 등록한다.
2. 운영 또는 모의투자용 앱을 준비하고 해당 환경의 App Key와 Secret Key를 발급한다. 포털에서 접속 허용 IP 설정을 요구하면 프로그램이 실행될 컴퓨터/서버의 공인 출발지 IP를 등록한다.
3. 모의투자를 사용할 경우 모의투자 계좌·이용신청 상태를 확인한다. 운영용 키와 모의용 키를 혼용하지 않는다.
4. 키는 로컬 .env 또는 운영체제 환경변수에 입력한다. 채팅·Notion·공개 GitHub·Drive에 키나 토큰을 올리지 않는다.

기본 환경은 mock이다. real을 선택해도 이번 코드에는 매수·매도·정정·취소 요청 경로가 없다. 기존 trader.py의 체결도 계속 로컬 paper 실행이다. 계좌 잔고·주문·체결 연계는 다음 단계다.

## 5. Windows 설치와 DB 변경

기존 사용자는 git pull로 갱신하고 가상환경 패키지를 설치한다.

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Workbench에서 갱신된 schema.sql을 다시 실행한다. CREATE TABLE IF NOT EXISTS가 새 market_quotes 테이블을 추가한다. 기존 runs 기록은 삭제하지 않는다. v0.1의 DB 전체 SELECT·INSERT·UPDATE 권한이면 사용 가능하다.

기존 .env에 아래 항목을 추가한다. 실제 키는 해당 PC에서만 넣는다.

```text
KIWOOM_ENV=mock
KIWOOM_APP_KEY=발급받은_해당환경_앱키
KIWOOM_SECRET_KEY=발급받은_해당환경_시크릿키
KIWOOM_WS_PATH=/api/dostk/websocket
```

최신 공식 국내주식 0B 예제의 WebSocket 경로는 /api/dostk/websocket이다. 일부 기존 포털 가이드에는 /api/websocket이 남아 있다. 기본값으로 연결이 안 될 때는 현재 계정에 안내된 경로를 확인하고 KIWOOM_WS_PATH=/api/websocket으로 명시 변경할 수 있다. 두 경로를 자동 순회하며 접속하지 않는다. 운영·모의 호스트는 코드에서 고정한다.

## 6. 인증과 수동 스냅샷 조회

```powershell
.\.venv\Scripts\python.exe market_data.py --env mock auth
.\.venv\Scripts\python.exe market_data.py --env mock snapshot --symbols 005930 000660
```

인증 성공 시 “인증 성공”만 출력하고 토큰은 표시하지 않는다. 토큰은 실행 중 메모리에서 만료일까지 재사용한다. 별도 CLI 실행은 새 프로세스이므로 다시 발급할 수 있다. 장기 운영 시 하나의 수집기를 유지한다.

snapshot 명령은 ka10001의 cur_prc와 flu_rt를 읽고 MySQL에 저장한다. 현재가의 +/− 부호는 대비 표시이므로 가격은 절댓값으로 정규화한다. REST 응답에는 여기서 사용할 체결시각이 없어 스냅샷 수신시각을 기록하며 최신 체결시세라고 표시하지 않는다.

한 번에 여러 종목의 초기값을 받으려면 다음을 실행한다.

```powershell
.\.venv\Scripts\python.exe market_data.py --env mock batch-snapshot --symbols 005930 000660
```

2종목이면 ka10095 조회 요청 1회다(토큰 발급 별도). 지원되지 않는 환경이면 확인 후 위 단일 snapshot 명령을 사용할 수 있다. 실패했다고 자동으로 많은 개별 요청을 발송하지 않는다.

## 7. 20종목 실시간 구독

PowerShell 창 A에서 실행한다. 아래 20개 코드는 구독 사용법 예시이며 추천 목록이 아니다.

```powershell
.\.venv\Scripts\python.exe market_data.py --env mock stream --symbols 005930 000660 035420 035720 005380 000270 051910 006400 068270 207940 105560 055550 086790 032830 015760 017670 030200 066570 003550 010950 --seconds 3600
```

1개의 등록 패킷으로 최대 200종목을 받는다. 서버 PING·LOGIN·REG 응답을 처리한 뒤 체결 이벤트를 캐시에 적재한다. 프로그램은 지정한 시간 뒤 종료하며 Ctrl+C로도 종료할 수 있다. 창 B에서 DB 캐시를 조회한다. 이 명령은 키움 서버를 호출하지 않는다.

```powershell
.\.venv\Scripts\python.exe market_data.py show --source kiwoom_ws_mock --symbols 005930 000660
```

실제 수신된 경우 source=kiwoom_ws_mock, data_kind=stream과 시간대가 포함된 시각이 표시된다. 장외이거나 체결이 없으면 캐시가 없다는 오류가 정상적으로 날 수 있다. snapshot은 kiwoom_rest_mock이라는 별도 source로 조회한다. mock과 real 시세도 별도 저장한다.

최신 캐시를 추천 파이프라인에 사용할 때:

```powershell
.\.venv\Scripts\python.exe trader.py run --mode approval --quotes-source mysql --market-source kiwoom_ws_mock --symbols 005930 000660
.\.venv\Scripts\python.exe trader.py approve 출력된_UUID
```

v0.2 캐시 후보 순위는 0B의 당일 등락률을 사용한다. 과거 여러 일의 모멘텀을 계산한 것이 아니며 ranking_metric=day_change_rate로 구별한다. 가격이 30만원을 넘는 주문은 기존 한도에 따라 중단된다. 모든 주문은 로컬 모의체결이다.

승인할 때 원래 추천의 시세가 10초를 넘으면 보류된다. 자동으로 새 가격으로 바꿔 체결하지 않는다. 새 추천을 생성해 다시 확인한다. 사람의 긴 승인 대기까지 지원하려면 후속 단계에서 주문 직전 재견적·가격변동 허용범위·재승인 절차를 구현한다.

## 8. API 없는 CSV 입력

다음 예제는 정해진 과거시각의 합성 자료다. 종목코드를 숫자로 변환하지 않도록 6자리 문자열을 유지한다.

```csv
symbol,name,price,change_rate,observed_at
005930,삼성전자_합성예시,70000,4.0,2026-11-12T15:30:00+09:00
```

change_rate는 비율 0.04가 아니라 퍼센트 4.0이다. observed_at에는 실제 자료 기준시각과 시간대를 넣는다.

```powershell
.\.venv\Scripts\python.exe market_data.py import-csv data/quotes_sample.csv
.\.venv\Scripts\python.exe market_data.py show --source csv --symbols 005930 000660
```

두 명령은 키움 키가 없어도 MySQL 설정만 있으면 실행할 수 있다. CSV는 source=csv, data_kind=historical로 저장된다. 파일을 방금 가져왔더라도 원자료가 최신 시세로 바뀌는 것은 아니다. 추천 실행의 market-source 선택지에 CSV를 넣지 않았고 실행용 검사도 CSV를 거부한다.

## 9. Ubuntu 수집기 실행

기존 venv와 DB를 준비한 뒤 .env를 설정하고 다음을 실행한다.

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python market_data.py --env mock auth
.venv/bin/python market_data.py --env mock stream --symbols 005930 000660 --seconds 3600
```

이번 수집기는 외부에 웹 포트를 열지 않는다. MySQL 서버는 기존 연결 설정을 사용한다. 한 환경의 수집기는 같은 MySQL DB를 공유하며 하나만 실행한다. 로그·수집 실패 시각·재시작 정책을 확인한 후 상주 서비스 구성을 진행한다.

## 10. 검증과 문제 해결

로컬 단위/비동기 테스트 16개 통과: 모의 20회 호출 간격, 다종목 단일 요청·누락 검사, 종목 일괄등록, 200개 제한, 음수 가격 정규화, stale 차단, CSV 차단, 승인 시 재검사, 토큰 재사용, REST 헤더와 주문경로 거부, 오류 중단, PING 응답, LOGIN/REG/REAL/캐시 flush 및 기존 추천 흐름을 확인했다. 테스트에는 가짜 HTTP/WebSocket과 메모리 시계를 사용했다.

실제 MySQL 8.0.46 접속, 키움 서버 인증·수신, 네트워크 재연결·과부하의 실환경 검증은 아직 하지 못했다. 다음 명령으로 사용자 환경의 코드 검사를 반복하고, 장중에 auth → snapshot → stream → show 순서로 통합 확인한다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

| 증상 | 확인 |
|---|---|
| 인증 실패 | mock/real에 맞는 키, 서비스 신청, 허용 IP, 키 만료 |
| WS 연결 실패 | 포털의 현재 경로, 10000 포트의 외부 접속, 기존 세션 |
| REG 실패 | 종목 수/코드, 등록 타입, 서버 오류코드 |
| 캐시 없음 | 장중 체결 여부, 올바른 source, DB 테이블 |
| 오래된 시세 | 수집기 연결, 최근 체결시각, PC 시간 동기화 |
| 수집기 이미 실행 | 기존 수집기에서 캐시 공유, 중복 종료 후 재실행 |
| HTTP 429 | 같은 계좌의 다른 프로그램 포함 호출량 확인, 간격 확대 |

## 11. 공식 출처

- [키움 호출 한도·세션·200종목 안내](https://openapi.kiwoom.com/intro?dummyVal=0)
- [키움 토큰 필드와 API 가이드](https://openapi.kiwoom.com/m/guide/apiguide)
- [공식 주식체결 0B 예제](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/실시간시세/subscribe_domestic_stock_trade_async.py)
- [공식 주식기본정보 ka10001 예제](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/종목정보/get_domestic_stock_info.py)
- [공식 WebSocket 로그인·PING 구현](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom/core/ws_client.py)
- [KRX 데이터 수신 방법과 계약 안내](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA003.jsp)
- [금융위원회 주식시세정보: 실제 갱신 설명 및 이용조건](https://www.data.go.kr/data/15094808/openapi.do)

- [공식 ka10095 다종목 조회 예제](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/종목정보/get_domestic_watchlist_stock_info.py)
