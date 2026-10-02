"""Focused component/HTTP checks. No training, native replay or live mutations."""
import copy,json,math,re,tempfile,threading,time,unittest
from pathlib import Path
from http.server import HTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import core
import server
ROOTS,PROVENANCE=core.load_roots()
COUNTS={'matched_scenario_runs':0,'receipt_checked_transitions':0}

class FormulaTests(unittest.TestCase):
    def test_arithmetic_and_condition(self):
        env={x:0. for x in core.NAMES};env.update(d_floor=2,d_best=-1,repair_weight=3,add_weight=.25)
        self.assertEqual(core.Formula('repair_weight*d_floor+add_weight*d_best')(env),5.75)
        self.assertEqual(core.Formula('max(1,2) if d_floor > 0 and d_best < 0 else abs(-9)')(env),2)
    def test_forbidden_syntax(self):
        for text in ["__import__('os')",'floor.__class__','[x for x in []]','open(1)','2**100000','(lambda:1)()',"'text'",'1e999','winner','target','sum(1,2)','min(1)']:
            with self.subTest(text=text),self.assertRaises(core.FormulaError):core.Formula(text)
    def test_division_zero_is_error(self):
        with self.assertRaises(core.FormulaError):core.Formula('1/gap')({'gap':0})
    def test_excessive_size_and_nesting(self):
        for text in ['1+'*400+'1','-'*100+'1']:
            with self.assertRaises(core.FormulaError):core.Formula(text)
    def test_nan_and_boolean_config_rejected(self):
        for d in [{'add_weight':math.nan},{'repair_weight':True},{'best_trigger':-1},{'live':True}]:
            with self.assertRaises(ValueError):core.config(d)

class WorldTests(unittest.TestCase):
    def test_frozen_source(self):
        self.assertEqual(len(ROOTS),7)
        self.assertFalse(PROVENANCE['native_hft']);self.assertFalse(PROVENANCE['live_authority'])
    def test_all_roots_matched_keep(self):
        c=core.config({'formula':'-is_add-is_repair-is_cancel-is_active'})
        for root in ROOTS:
            for scenario in core.SCENARIOS:
                r=core.experiment(root,c,scenario,[],3)
                self.assertEqual(r['baseline'],[{k:v for k,v in row.items() if k!='stop_latched'} for row in r['candidate']])
                self.assertEqual(r['delta'],{'UP':0.,'DOWN':0.})
                COUNTS['matched_scenario_runs']+=1;COUNTS['receipt_checked_transitions']+=6
    def test_default_candidate_all_roots(self):
        for root in ROOTS:
            for scenario in core.SCENARIOS:
                r=core.experiment(root,core.DEFAULT,scenario,[],8)
                self.assertEqual(len(r['candidate']),9)
                self.assertFalse(r['training_eligible']);self.assertFalse(r['realized_pnl_claim'])
                COUNTS['matched_scenario_runs']+=1;COUNTS['receipt_checked_transitions']+=16
    def test_manual_legal_and_illegal(self):
        n=core.prepare(ROOTS[0],core.DEFAULT)
        z,_,rows=core.replay_prefix(ROOTS[0],core.DEFAULT,'WHIPSAW',[0])
        self.assertEqual(len(rows),2)
        bad=next((p['id'] for p in core.actions(n) if not p['legal']),None)
        if bad is not None:
            with self.assertRaises(ValueError):core.replay_prefix(ROOTS[0],core.DEFAULT,'WHIPSAW',[bad])
    def test_history_and_horizon_limits(self):
        for history in [[0]*13,[999],[False]]:
            with self.assertRaises(ValueError):core.replay_prefix(ROOTS[0],core.DEFAULT,'UP',history)
        with self.assertRaises(ValueError):core.experiment(ROOTS[0],core.DEFAULT,'UP',[],100)
    def test_unknown_owner_remains_after_stop(self):
        root=copy.deepcopy(ROOTS[0]);n=root['node'];n['state']['owners']=[{'key':'test_unknown','side':'UP','route':'ACTIVE','qty':2.,'limit':.8,'state':'UNKNOWN'}]
        z,latched,_=core.replay_prefix(root,core.DEFAULT,'NO_FILL',['STOP',0,0])
        self.assertTrue(latched);self.assertEqual(len(z['state']['owners']),1)
        self.assertAlmostEqual(core.view(z)['pending_cash']['UP'],1.6)
        self.assertAlmostEqual(core.view(z)['pending_floor'],min(core.view(z)['payoff']['UP'],core.view(z)['payoff']['DOWN']-1.6))
    def test_stop_does_not_emit_new_orders(self):
        n=core.prepare(ROOTS[0],core.DEFAULT)
        self.assertEqual(core.stop_action(n)['orders'],[])
        r=core.experiment(ROOTS[0],core.DEFAULT,'NO_FILL',['STOP'],3)
        self.assertTrue(all(row.get('stop_latched') for row in r['candidate'][1:]))
        self.assertEqual(r['delta'],{'UP':0.,'DOWN':0.})
    def test_confirmed_and_pending_safe_are_different(self):
        n=core.prepare(ROOTS[0],core.DEFAULT);n['state']['inv']={'UP':170.,'DOWN':90.};n['state']['cost']=100.
        n['state']['owners']=[{'key':'pending','side':'UP','route':'PASSIVE','qty':15.,'limit':.5,'state':'CANCEL_PENDING'}]
        core.k.refresh(n)
        self.assertTrue(core.eligible_stop(n,core.config({'trigger':'CONFIRMED'})))
        self.assertFalse(core.eligible_stop(n,core.config({'trigger':'PENDING_SAFE'})))
    def test_source_roots_not_mutated(self):
        before=json.dumps(ROOTS,sort_keys=True)
        core.experiment(ROOTS[0],core.DEFAULT,'DOWN',[0],3)
        self.assertEqual(before,json.dumps(ROOTS,sort_keys=True))

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.ROOTS=ROOTS;server.PROVENANCE=PROVENANCE
        cls.http=HTTPServer(('127.0.0.1',0),server.Handler)
        cls.t=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.t.start()
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)
    @classmethod
    def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.t.join()
    def request(self,path,data=None,headers=None):
        hs={'Origin':self.base,'Content-Type':'application/json','X-Playground-Token':server.TOKEN}
        hs.update(headers or {})
        req=Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers=hs)
        try:
            with urlopen(req,timeout=5) as r:return r.status,r.read(),dict(r.headers)
        except HTTPError as e:return e.code,e.read(),dict(e.headers)
    def payload(self):return {'root_id':ROOTS[0]['id'],'scenario':'WHIPSAW','config':{},'history':[],'horizon':3}
    def test_page_and_assets(self):
        for p in ['/','/app.js','/app.css','/health','/api/catalog']:
            code,_,hs=self.request(p);self.assertEqual(code,200);self.assertIn('Content-Security-Policy',hs)
    def test_no_file_browsing(self):self.assertEqual(self.request('/../AGENTS.md')[0],404)
    def test_no_cross_origin(self):self.assertEqual(self.request('/api/state',self.payload(),{'Origin':'https://example.com'})[0],403)
    def test_no_missing_token(self):self.assertEqual(self.request('/api/state',self.payload(),{'X-Playground-Token':''})[0],403)
    def test_no_invalid_host(self):self.assertEqual(self.request('/health',headers={'Host':'other.example'})[0],403)
    def test_state_and_compare(self):
        for path in ['/api/state','/api/compare']:
            code,data,_=self.request(path,self.payload());self.assertEqual(code,200,data)
            self.assertFalse(json.loads(data)['training_eligible'])
    def test_bad_formula_rejected(self):
        body=self.payload();body['config']={'formula':'1 / 0'}
        self.assertEqual(self.request('/api/compare',body)[0],400)
    def test_no_dispatch_endpoint(self):self.assertEqual(self.request('/api/dispatch',self.payload())[0],404)
    def test_export_not_submit(self):
        old=core.STORE
        with tempfile.TemporaryDirectory(prefix='test_',dir=old) as d:
            core.STORE=Path(d)
            try:
                p=self.payload();p.update(kind='NATIVE_VALIDATION_REQUEST',note='component test only')
                code,data,_=self.request('/api/export',p);self.assertEqual(code,200,data)
                z=json.loads(data);self.assertEqual(z['artifact']['status'],'SAVED_NOT_SUBMITTED')
                self.assertEqual(z['artifact']['evaluation_contract']['native_adapter_status'],'NOT_CONNECTED_IN_V0')
                self.assertTrue((core.ROOT/z['path']).is_file())
                self.assertEqual(core.sha(core.ROOT/z['path']),z['sha256'])
            finally:core.STORE=old

if __name__=='__main__':
    started=time.monotonic();suite=unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'status':'PASS' if result.wasSuccessful() else 'FAIL','unittest_cases':result.testsRun,
            'failures':len(result.failures),'errors':len(result.errors),**COUNTS,'seconds':time.monotonic()-started,
            'native_hft_runs':0,'training_updates':0,'worker_jobs':0,'live_changes':0,'evidence':'COMPONENT_AND_LOCAL_HTTP_ONLY'}
    core.STORE.mkdir(parents=True,exist_ok=True)
    (core.STORE/'VALIDATION.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report));raise SystemExit(0 if result.wasSuccessful() else 1)
