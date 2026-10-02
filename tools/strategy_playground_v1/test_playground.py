"""Local contract, security, and recorded-native-playback consistency checks.
Does not start native work, fit a model, or change any live service.
"""
import hashlib,json,math,threading,time,unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from settings import validate,validate_request,offsets,DEFAULT,MODEL_PINS
import transport as t
import server

class SettingsTests(unittest.TestCase):
    def test_zero_offsets(self):self.assertEqual(offsets(validate({})),[0.]*14)
    def test_roles_not_order_sizes(self):
        c=validate({'add_bias':-.5,'repair_bias':.25,'active_bias':.1,'keep_bias':.2});a=offsets(c)
        self.assertEqual(a[7],-.5);self.assertEqual(a[9],-.5);self.assertEqual(a[0],.2)
        self.assertEqual(a[2],.25);self.assertAlmostEqual(a[6],.35)
    def test_invalid_configs(self):
        for d in [{'seed':True},{'seed':1},{'add_bias':math.nan},{'active_bias':math.inf},{'repair_bias':6},
                  {'keep_bias':True},{'clock_mode':'ONE_SECOND'},{'addition_mode':'WINNER'},{'eval':'1+1'}]:
            with self.subTest(d=d),self.assertRaises(ValueError):validate(d)
    def test_request_time_not_playback_speed(self):
        r=validate_request({'market':2312596,'apply_ms':80000});self.assertEqual(r['apply_ms'],80000)
        for k in ['speed','steps','horizon','target','winner','worker_host','shell']:
            with self.assertRaises(ValueError):validate_request({'market':2312596,k:1})
    def test_time_bounds(self):
        for n in [-1,300000,math.inf,True]:
            with self.assertRaises(ValueError):validate_request({'market':2312596,'apply_ms':n})
    def test_note_is_not_executable(self):
        s="__import__('os').system('not executed')"
        self.assertEqual(validate_request({'market':2312596,'note':s})['note'],s)
    def test_two_frozen_models_only(self):self.assertEqual(set(MODEL_PINS),{20260920,20260921})
    def test_manifest_native_source_hashes(self):
        m=t.read(t.P/'MANIFEST.json')
        self.assertEqual(len(m['legacy_files']),49)
        for f,h in m['files'].items():self.assertEqual(t.sha(t.P/f),h,f)

class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results=[(p.parent,t.read(p)) for p in (t.STORE/'jobs').glob('*/RESULT.json')]
        if not cls.results:raise AssertionError('No actual native output was collected')
        cls.base=t.read(cls.results[0][0]/'baseline.json')
    def test_actual_market_clock(self):
        b=self.base;self.assertEqual(b['end_ms']-b['start_ms'],300000)
        self.assertTrue(all(a['ms']<=b['ms'] for a,b in zip(b['frames'],b['frames'][1:])))
        self.assertGreater(b['frames'][0]['ms'],0)
    def test_every_state_uses_only_already_received_fills(self):
        for path,r in self.results:
            for name in r['files']:
                b=t.read(path/name);fills=b['fills'];i=0;inv={'UP':0.,'DOWN':0.};cash=0.
                for f in b['frames']:
                    while i<len(fills) and fills[i]['ms']<=f['ms']+1e-5:
                        x=fills[i];inv[x['side']]+=x['qty'];cash+=x['qty']*x['price']+x['fee'];i+=1
                    self.assertAlmostEqual(cash,f['cost'],places=6)
                    for side in inv:self.assertAlmostEqual(inv[side],f['inv'][side],places=6)
    def test_pending_is_reserved_until_terminal(self):
        for f in self.base['frames']:
            expected={s:sum(o['qty']*o['limit'] for o in f['owners'] if o['side']==s) for s in ('UP','DOWN')}
            for s in expected:self.assertAlmostEqual(expected[s],f['pending_cash'][s],places=6)
            floor=min(f['payoff']['UP']-expected['DOWN'],f['payoff']['DOWN']-expected['UP'])
            self.assertAlmostEqual(floor,f['pending_floor'],places=6)
    def test_final_not_backdated(self):
        self.assertGreaterEqual(self.base['final']['last_observation_ms'],self.base['duration_ms'])
        self.assertEqual(self.base['final']['last_observation_ms'],self.base['frames'][-1]['ms'])
    def test_collected_hashes_and_scope(self):
        for p,r in self.results:
            self.assertEqual(r['training_updates'],0);self.assertEqual(r['live_changes'],0)
            for f,h in r['files'].items():
                self.assertEqual(t.sha(p/f),h)
                self.assertTrue(t.read(p/f)['scope']['native_hft']);self.assertEqual(t.read(p/f)['scope']['fees'],0)
    def test_native_zero_control_evidence(self):
        rows=[r for p,r in self.results if r.get('audit',{}).get('prefix',{}).get('whole_path')]
        self.assertTrue(rows)
        self.assertTrue(all(r['audit']['prefix']['pass'] for r in rows))

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);cls.h.daemon_threads=True
        cls.thread=threading.Thread(target=cls.h.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.h.server_port)
    @classmethod
    def tearDownClass(cls):cls.h.shutdown();cls.h.server_close();cls.thread.join()
    def request(self,path,data=None,headers=None):
        h={'Content-Type':'application/json','Origin':self.base,'X-Playground-Token':server.TOKEN};h.update(headers or {})
        req=Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers=h)
        try:
            with urlopen(req,timeout=4) as r:return r.status,r.read(),dict(r.headers)
        except HTTPError as e:return e.code,e.read(),dict(e.headers)
    def test_assets_and_security_headers(self):
        for path in ['/','/app.js','/app.css','/health','/api/catalog']:
            code,_,h=self.request(path);self.assertEqual(code,200);self.assertIn('Content-Security-Policy',h)
    def test_host_rejected(self):self.assertEqual(self.request('/health',headers={'Host':'wrong.example'})[0],403)
    def test_cross_origin_rejected(self):self.assertEqual(self.request('/api/run',{}, {'Origin':'https://wrong.example'})[0],403)
    def test_token_rejected(self):self.assertEqual(self.request('/api/run',{}, {'X-Playground-Token':''})[0],403)
    def test_arbitrary_request_rejected_before_dispatch(self):
        for data in [{'market':2312596,'worker_host':'elsewhere'},{'market':2312596,'candidate':{'shell':'x'}},{'market':False}]:
            self.assertEqual(self.request('/api/run',data)[0],400)
    def test_paths_and_live_routes_absent(self):
        for path in ['/../AGENTS.md','/api/live-rules','/api/order','/api/enable']:
            self.assertEqual(self.request(path)[0],404)
    def test_playback_id_validation(self):self.assertEqual(self.request('/api/playback?job_id=../../elsewhere&which=baseline')[0],400)
    def test_no_model_weight_write_api(self):self.assertEqual(self.request('/api/train',{})[0],404)

if __name__=='__main__':
    start=time.monotonic();suite=unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    payload={'status':'PASS' if result.wasSuccessful() else 'FAIL','tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'seconds':time.monotonic()-start,'native_jobs_started':0,'training_updates':0,'live_changes':0}
    t.save(t.STORE/'LOCAL_VALIDATION.json',payload);print(json.dumps(payload));raise SystemExit(0 if result.wasSuccessful() else 1)
