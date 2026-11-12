import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_data import (DataError, KiwoomReadClient, Quote, RateLimiter,
                         parse_tick, read_csv, recv_packet, registration, session)
from trader import fill, recommend

NOW = datetime(2026, 11, 13, 0, 0, 1, tzinfo=timezone.utc)

def tick():
    return {'item':'005930','type':'0B','values':{'20':'090001','10':'-70000','12':'4.0'}}

class MarketDataTests(unittest.TestCase):
    def test_20_symbols_mock_spacing(self):
        now = [0.0]
        limiter = RateLimiter(1.05, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0]+s))
        starts = []
        for _ in range(20):
            limiter.wait()
            starts.append(now[0])
        self.assertAlmostEqual(starts[-1], 19.95)
        self.assertTrue(all(b-a >= 1.049 for a,b in zip(starts,starts[1:])))

    def test_registration_batches_20_and_limits(self):
        symbols = [f'{i:06}' for i in range(20)]
        self.assertEqual(len(registration(symbols)['data'][0]['item']),20)
        with self.assertRaises(DataError):
            registration([f'{i:06}' for i in range(201)])
        with self.assertRaises(DataError):
            registration(['005930_NX'])

    def test_tick_price_sign_and_time(self):
        q = parse_tick(tick(),'mock',NOW)
        self.assertEqual(q.price,70000)
        self.assertEqual(q.change_rate,4.0)
        q.assert_fresh_stream(now=NOW)
        with self.assertRaises(DataError):
            q.assert_fresh_stream(now=NOW+timedelta(seconds=11))

    def test_csv_is_never_executable_even_when_recent(self):
        q = Quote('005930',70000,4.0,'csv',NOW.isoformat(),NOW.isoformat(),'historical')
        with self.assertRaises(DataError):
            q.assert_fresh_stream(now=NOW)
        with self.assertRaises(DataError):
            recommend('auto',cached_quotes=[q])

    def test_approval_rechecks_age(self):
        q = parse_tick(tick(),'mock',NOW)
        record = {'data_kind':'stream','orders':[{'quote':asdict(q),'price':70000,'qty':1}]}
        with patch('market_data.utcnow', return_value=NOW+timedelta(seconds=11)):
            with self.assertRaises(DataError):
                fill(record)
        self.assertNotIn('state',record)

    def test_token_reuse_and_rest_headers(self):
        seen = []
        def opener(req, timeout):
            seen.append(req)
            if req.full_url.endswith('/oauth2/token'):
                return io.StringIO(json.dumps({'token':'test-token','expires_dt':'20991231235959','return_code':0}))
            return io.StringIO(json.dumps({'cur_prc':'-70000','flu_rt':'4.0','return_code':0}))
        with patch.dict(os.environ, {'KIWOOM_APP_KEY':'test-key','KIWOOM_SECRET_KEY':'test-secret'},clear=True):
            client = KiwoomReadClient(opener=opener)
            client.limiter.wait = lambda: None
            self.assertEqual(client.token(),client.token())
            q = client.snapshot('005930')
            self.assertEqual(q.data_kind,'snapshot')
            self.assertEqual(len(seen),2)
            self.assertEqual(seen[1].get_header('Api-id'),'ka10001')
            self.assertEqual(seen[1].get_header('Authorization'),'Bearer test-token')
            self.assertTrue(seen[1].full_url.startswith('https://mockapi.kiwoom.com/'))
            with self.assertRaises(DataError):
                client._post('/api/dostk/ordr', {})

    def test_invalid_api_response_stops(self):
        with patch.dict(os.environ, {}, clear=True):
            client = KiwoomReadClient(opener=lambda *a,**k: io.StringIO('{"return_code":429}'))
            with self.assertRaises(DataError):
                client._post('/api/dostk/stkinfo',{})

    def test_batch_uses_one_request_and_checks_missing_rows(self):
        with patch.dict(os.environ, {}, clear=True):
            client = KiwoomReadClient()
            client.token = lambda: 'test-token'
            response = {'atn_stk_infr': [{'stk_cd':s,'cur_prc':'70000','flu_rt':'1.0'}
                                        for s in ['005930','000660']]}
            with patch.object(client, '_post', return_value=response) as post:
                self.assertEqual(len(client.batch_snapshot(['005930','000660'])),2)
                self.assertEqual(post.call_count,1)
                self.assertEqual(post.call_args.args[1]['stk_cd'],'005930|000660')
                self.assertEqual(post.call_args.args[2]['api-id'],'ka10095')
                with self.assertRaises(DataError):
                    client.batch_snapshot(['005930'])

    def test_csv_reader_keeps_leading_zero(self):
        quotes = read_csv(Path(__file__).parents[1]/'data/quotes_sample.csv')
        self.assertEqual(quotes[0].symbol,'005930')
        self.assertEqual(quotes[0].source,'csv')

class WebsocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_ping_echo(self):
        class WS:
            sent=[]
            packets=iter(['{"trnm":"PING"}','{"trnm":"REG","return_code":0}'])
            async def recv(self): return next(self.packets)
            async def send(self,value): self.sent.append(json.loads(value))
        ws=WS()
        result=await recv_packet(ws)
        self.assertEqual(ws.sent,[{'trnm':'PING'}])
        self.assertEqual(result['trnm'],'REG')

    async def test_login_registration_and_cache_flush(self):
        class WS:
            def __init__(self):
                self.sent=[]
                self.packets=[{'trnm':'LOGIN','return_code':0},{'trnm':'REG','return_code':0},
                              {'trnm':'REAL','data':[tick()]}]
            async def recv(self):
                if self.packets: return json.dumps(self.packets.pop(0))
                await asyncio.sleep(10)
            async def send(self,value): self.sent.append(json.loads(value))
        class Client:
            mode='mock'
            def token(self): return 'test-token'
        class Cache:
            def __init__(self): self.quotes=[]
            def put_many(self,quotes): self.quotes.extend(quotes)
        ws, cache=WS(), Cache()
        await session(ws,Client(),['005930'],cache,0.02)
        self.assertEqual(ws.sent[0]['trnm'],'LOGIN')
        self.assertEqual(ws.sent[1]['data'][0]['item'],['005930'])
        self.assertEqual(cache.quotes[0].symbol,'005930')

if __name__=='__main__':
    unittest.main()
