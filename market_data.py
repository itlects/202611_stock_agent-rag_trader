"""키움 읽기 전용 연결, 실시간 수집, MySQL 캐시, API 없는 CSV 분석 입력."""
import argparse
import asyncio
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

UTC = timezone.utc
KST = timezone(timedelta(hours=9))

class DataError(RuntimeError):
    pass


def utcnow():
    return datetime.now(UTC)


def symbols_checked(symbols):
    result = list(dict.fromkeys(symbols))
    if not result or len(result) > 200 or any(not re.fullmatch(r'[0-9]{6}', s) for s in result):
        raise DataError('KRX 6자리 종목코드 1~200개를 지정하세요')
    return result


def timestamp(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise DataError('시각에 시간대(+09:00 또는 +00:00)가 필요합니다')
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: int
    change_rate: float
    source: str
    observed_at: str
    received_at: str
    data_kind: str
    venue: str = 'KRX'
    name: str = ''

    def __post_init__(self):
        symbols_checked([self.symbol])
        if type(self.price) is not int or self.price <= 0 or not math.isfinite(self.change_rate):
            raise DataError('유효하지 않은 가격/등락률')
        timestamp(self.observed_at)
        timestamp(self.received_at)

    def assert_fresh_stream(self, max_age=10, now=None):
        now = now or utcnow()
        if max_age <= 0 or not math.isfinite(max_age):
            raise DataError('max_age는 양수여야 합니다')
        if not self.source.startswith('kiwoom_ws_') or self.data_kind != 'stream':
            raise DataError('CSV/REST 스냅샷은 실행 시세로 사용할 수 없습니다')
        for value in (self.observed_at, self.received_at):
            age = (now - timestamp(value)).total_seconds()
            if age < -2 or age > max_age:
                raise DataError('시세가 오래되었거나 미래 시각입니다. 모의체결 보류')


class RateLimiter:
    """한 수집 프로세스가 사용하는 보수적 전역 간격. 여러 프로세스와는 공유되지 않음."""
    def __init__(self, interval, clock=time.monotonic, sleep=time.sleep):
        self.interval = interval
        self.clock, self.sleep = clock, sleep
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            delay = self.next_at - self.clock()
            if delay > 0:
                self.sleep(delay)
            self.next_at = self.clock() + self.interval


class KiwoomReadClient:
    def __init__(self, mode='mock', opener=urlopen):
        if mode not in ('mock', 'real'):
            raise DataError('KIWOOM_ENV는 mock 또는 real')
        self.mode = mode
        host = 'mockapi.kiwoom.com' if mode == 'mock' else 'api.kiwoom.com'
        self.base = 'https://' + host
        path = os.getenv('KIWOOM_WS_PATH', '/api/dostk/websocket')
        if path not in ('/api/dostk/websocket', '/api/websocket'):
            raise DataError('허용하지 않는 WebSocket 경로')
        self.ws_url = 'wss://' + host + ':10000' + path
        self.opener = opener
        minimum = 1.05 if mode == 'mock' else 0.26
        interval = float(os.getenv('KIWOOM_QUERY_INTERVAL', str(minimum)))
        if not math.isfinite(interval) or interval < minimum:
            raise DataError(f'조회 간격은 최소 {minimum}초')
        self.limiter = RateLimiter(interval)
        self.token_value = None
        self.expiry = datetime.min.replace(tzinfo=UTC)

    def _post(self, path, body, headers=None):
        # 이 클래스는 인증과 종목조회 두 경로만 허용한다. 주문 경로는 없다.
        if path not in ('/oauth2/token', '/api/dostk/stkinfo'):
            raise DataError('읽기 전용 클라이언트에서 허용하지 않는 경로')
        for attempt in range(4):
            self.limiter.wait()
            req = Request(self.base + path, data=json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json;charset=UTF-8', **(headers or {})})
            try:
                with self.opener(req, timeout=20) as res:
                    data = json.load(res)
            except HTTPError as exc:
                if attempt < 3 and (exc.code == 429 or 500 <= exc.code < 600):
                    wait = 2 ** attempt
                    retry_after = exc.headers.get('Retry-After', '') if exc.headers else ''
                    if retry_after.isdigit():
                        wait = max(wait, int(retry_after))
                    if wait > 30:
                        raise DataError('서버 재시도 대기시간이 큽니다. 잠시 후 재실행하세요') from None
                    time.sleep(wait)
                    continue
                raise DataError(f'키움 HTTP {exc.code}: 키/환경/접속 IP를 확인하세요') from None
            except (OSError, ValueError):
                raise DataError('키움 네트워크/JSON 응답 오류') from None
            if not isinstance(data, dict) or str(data.get('return_code', 0)) != '0':
                # 응답 전문에는 민감정보가 포함될 수 있어 출력하지 않는다.
                code = data.get('return_code', 'unknown') if isinstance(data, dict) else 'invalid'
                raise DataError(f'키움 응답 오류 코드 {code}; 공식 오류코드를 확인하세요')
            return data
        raise DataError('조회 재시도 횟수 초과')

    def token(self):
        if self.token_value and utcnow() < self.expiry - timedelta(seconds=60):
            return self.token_value
        key, secret = os.getenv('KIWOOM_APP_KEY'), os.getenv('KIWOOM_SECRET_KEY')
        if not key or not secret:
            raise DataError('로컬 .env에 KIWOOM_APP_KEY / KIWOOM_SECRET_KEY를 설정하세요')
        data = self._post('/oauth2/token', {'grant_type': 'client_credentials', 'appkey': key, 'secretkey': secret})
        try:
            expiry = datetime.strptime(data['expires_dt'], '%Y%m%d%H%M%S').replace(tzinfo=KST).astimezone(UTC)
            token = data['token']
            if not isinstance(token, str) or not token or expiry <= utcnow() + timedelta(seconds=60):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise DataError('토큰/만료일 응답이 유효하지 않습니다') from None
        self.token_value, self.expiry = token, expiry
        return token

    def snapshot(self, symbol):
        symbols_checked([symbol])
        data = self._post('/api/dostk/stkinfo', {'stk_cd': symbol},
                          {'authorization': 'Bearer ' + self.token(), 'api-id': 'ka10001', 'cont-yn': 'N', 'next-key': ''})
        now = utcnow().isoformat()
        return Quote(symbol, abs(int(data['cur_prc'].replace(',', ''))), float(data['flu_rt']),
                     'kiwoom_rest_' + self.mode, now, now, 'snapshot', name=data.get('stk_nm', ''))

    def batch_snapshot(self, symbols):
        symbols = symbols_checked(symbols)
        if len(symbols) > 20:
            raise DataError('이 구현의 일괄 스냅샷은 최대 20개. 더 큰 목록은 나누어 요청하세요')
        data = self._post('/api/dostk/stkinfo', {'stk_cd': '|'.join(symbols)},
                          {'authorization': 'Bearer ' + self.token(), 'api-id': 'ka10095',
                           'cont-yn': 'N', 'next-key': ''})
        rows = data.get('atn_stk_infr')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DataError('ka10095 응답 형식 오류. 현재 API 상세 명세를 확인하세요')
        now = utcnow().isoformat()
        result = [Quote(row['stk_cd'], abs(int(row['cur_prc'].replace(',', ''))), float(row['flu_rt']),
                        'kiwoom_rest_' + self.mode, now, now, 'snapshot', name=row.get('stk_nm', ''))
                  for row in rows]
        if {q.symbol for q in result} != set(symbols):
            raise DataError('일괄 응답 종목이 요청과 다릅니다. 누락·연속조회 여부를 확인하세요')
        return result


def registration(symbols):
    return {'trnm': 'REG', 'grp_no': '1', 'refresh': '0',
            'data': [{'item': symbols_checked(symbols), 'type': ['0B']}]}


def parse_tick(item, mode, now=None):
    now = now or utcnow()
    if item.get('type') != '0B':
        return None
    values = item['values']
    # 0B는 체결시각만 제공. 수신일의 한국 날짜와 결합한다.
    local = now.astimezone(KST)
    t = datetime.strptime(values['20'], '%H%M%S').time()
    event = datetime.combine(local.date(), t, KST)
    if event > local + timedelta(minutes=5):
        event -= timedelta(days=1)
    return Quote(item['item'], abs(int(values['10'].replace(',', ''))), float(values['12']),
                 'kiwoom_ws_' + mode, event.astimezone(UTC).isoformat(), now.isoformat(), 'stream')


class QuoteCache:
    def __init__(self):
        import mysql.connector
        self.db = mysql.connector.connect(host=os.getenv('DB_HOST', '127.0.0.1'),
            port=int(os.getenv('DB_PORT', '3306')), database=os.getenv('DB_NAME', 'agent_rag_trader'),
            user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], autocommit=True)

    def collector_lock(self, mode):
        cur = self.db.cursor()
        try:
            cur.execute('SELECT GET_LOCK(%s, 0)', ('agent_rag_collector_' + mode,))
            if cur.fetchone()[0] != 1:
                raise DataError('같은 환경의 수집기가 이미 실행 중입니다. 기존 수집기를 사용하세요')
        finally:
            cur.close()

    def put_many(self, quotes):
        if not quotes:
            return
        cur = self.db.cursor()
        try:
            cur.executemany('INSERT INTO market_quotes(symbol,source,payload) VALUES(%s,%s,%s) '
                'ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=CURRENT_TIMESTAMP(6)',
                [(q.symbol, q.source, json.dumps(asdict(q), ensure_ascii=False)) for q in quotes])
        finally:
            cur.close()

    def get(self, symbols, source):
        result = []
        cur = self.db.cursor()
        try:
            for symbol in symbols_checked(symbols):
                cur.execute('SELECT payload FROM market_quotes WHERE symbol=%s AND source=%s', (symbol, source))
                row = cur.fetchone()
                if not row:
                    raise DataError(f'{symbol}: 요청한 공급원의 캐시가 없습니다')
                result.append(Quote(**json.loads(row[0])))
            return result
        finally:
            cur.close()

    def close(self):
        self.db.close()


def read_csv(path):
    # 사용자가 정당하게 확보한 파일만 사용한다. CSV는 항상 분석용으로 분류.
    result = []
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            result.append(Quote(row['symbol'], int(row['price']), float(row['change_rate']),
                'csv', row['observed_at'], utcnow().isoformat(), 'historical', name=row.get('name', '')))
    if not result:
        raise DataError('CSV 데이터가 비어 있습니다')
    return result


async def recv_packet(ws):
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=60)
        if raw == 'PING':
            await ws.send(raw)
            continue
        data = json.loads(raw)
        if data.get('trnm') == 'PING':
            await ws.send(json.dumps(data))
            continue
        return data


async def session(ws, client, symbols, cache, duration):
    await ws.send(json.dumps({'trnm': 'LOGIN', 'token': client.token()}))
    ack = await asyncio.wait_for(recv_packet(ws), timeout=20)
    if ack.get('trnm') != 'LOGIN' or str(ack.get('return_code', 'missing')) != '0':
        raise DataError('WebSocket 인증 실패. 키/환경/세션을 확인하세요')
    await ws.send(json.dumps(registration(symbols)))
    pending = {}
    stop_at = time.monotonic() + duration
    next_flush = time.monotonic() + 0.5
    try:
        while time.monotonic() < stop_at:
            try:
                data = await asyncio.wait_for(recv_packet(ws), timeout=min(0.5, max(0.01, stop_at-time.monotonic())))
            except asyncio.TimeoutError:
                data = {}
            if 'return_code' in data and str(data['return_code']) != '0':
                raise DataError('WebSocket 등록/수신 오류. 공식 오류코드를 확인하세요')
            if data.get('trnm') == 'REAL':
                for item in data.get('data', []):
                    if item.get('item') not in symbols:
                        continue
                    q = parse_tick(item, client.mode)
                    if q:
                        pending[q.symbol] = q
            if time.monotonic() >= next_flush and pending:
                cache.put_many(list(pending.values()))
                pending.clear()
                next_flush = time.monotonic() + 0.5
    finally:
        if pending:
            cache.put_many(list(pending.values()))


async def stream(client, symbols, cache, duration):
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed
    deadline = time.monotonic() + duration
    for attempt in range(4):
        try:
            # 공식 최신 예제의 국내주식 경로. 구형 가이드 경로는 .env로 명시 선택.
            async with connect(client.ws_url, open_timeout=20, ping_interval=None) as ws:
                await session(ws, client, symbols, cache, max(0, deadline-time.monotonic()))
            return
        except (ConnectionClosed, OSError, asyncio.TimeoutError):
            if attempt == 3 or time.monotonic() >= deadline:
                raise DataError('WebSocket 연결 실패/중단. 캐시 시각과 세션을 확인하세요') from None
            await asyncio.sleep(2 ** attempt)
    raise DataError('재연결 횟수 초과')


def main():
    from trader import load_env
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', choices=['mock', 'real'], default=os.getenv('KIWOOM_ENV', 'mock'))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('auth')
    for name in ('snapshot', 'batch-snapshot', 'stream', 'show'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--symbols', nargs='+', required=True)
        if name == 'stream':
            cmd.add_argument('--seconds', type=int, default=300)
        if name == 'show':
            cmd.add_argument('--source', default='kiwoom_ws_mock')
    imp = sub.add_parser('import-csv')
    imp.add_argument('path')
    args = parser.parse_args()
    cache = None
    try:
        if args.command == 'auth':
            client = KiwoomReadClient(args.env)
            client.token()
            print('인증 성공 (토큰은 표시/파일 저장하지 않음)')
            return
        cache = QuoteCache()
        if args.command == 'import-csv':
            quotes = read_csv(args.path)
            cache.put_many(quotes)
            print(f'{len(quotes)}건 CSV 저장 (분석용)')
        elif args.command == 'show':
            quotes = cache.get(args.symbols, args.source)
            print(json.dumps([asdict(q) for q in quotes], ensure_ascii=False, indent=2))
        else:
            symbols = symbols_checked(args.symbols)
            cache.collector_lock(args.env)
            client = KiwoomReadClient(args.env)
            if args.command == 'batch-snapshot':
                for start in range(0, len(symbols), 20):
                    quotes = client.batch_snapshot(symbols[start:start+20])
                    cache.put_many(quotes)
                    print(json.dumps([asdict(q) for q in quotes], ensure_ascii=False))
            elif args.command == 'snapshot':
                for symbol in symbols:
                    q = client.snapshot(symbol)
                    cache.put_many([q])
                    print(json.dumps(asdict(q), ensure_ascii=False))
            else:
                if not 1 <= args.seconds <= 86400:
                    raise DataError('--seconds는 1~86400')
                print(f'{len(symbols)}종목 구독 시작. REST 반복 조회 없음; Ctrl+C로 종료.', flush=True)
                asyncio.run(stream(client, symbols, cache, args.seconds))
    except (DataError, KeyError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except KeyboardInterrupt:
        print('수집 종료')
    finally:
        if cache:
            cache.close()

if __name__ == '__main__':
    main()
