import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from tools.hft244_pair_postpaid_trace_observer_v2 import make_observer, python_state

class Fake:
    def __init__(self,tape,arm):
        self.arm=arm;self.calls=[];self.inv={'UP':0.,'DOWN':0.};self.cost=0.
        self.un={'UP':[],'DOWN':[]};self.slot_key={};self.orders={};self.key_role={}
        self._probe_stage='UNSELECTED';self._probe_cross_blocks=0;self._probe_key=None
        self._receipt_ledger=SimpleNamespace(seen={})
    def _reanchor_stale(self,t):
        self.calls.append(('reanchor',t))
        if self.slot_key:self._request_cancel(t,1,'FAKE_REASON')
    def _role_decision(self,qv):
        self.calls.append(('role',));return 'UP','ECONOMIC_CORE',True,False
    def _pair_ok(self,side,p):
        self.calls.append(('pair',side,p));return p<=.5
    def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
        self.calls.append(('candidate',side,require_pair,require_budget))
        for p in (.6,.4):
            if self._pair_ok(side,p):return p,1/p,None
    def _submit_role(self,t,side,role,p,q,proj,source):
        self.calls.append(('submit',t,side,role,p,q,source))
        key='UP_1';self.orders[key]=dict(n=1,side=side,price=p,qty=q,cum=0.,placed=t,status='NONE')
        self.slot_key[1]=key;self.key_role[key]=role
        return True
    def _request_cancel(self,t,sid,reason):
        self.calls.append(('cancel',t,sid,reason));self.orders[self.slot_key[sid]]['cancelRequested']=True
        return True
    def _probe_submit(self,t,option):
        self.calls.append(('paid',t));self._probe_key='PAID_2';return None
    def _open_one_option(self,t,qv,end):
        self.calls.append(('open',t,end))
        side,role,pair,budget=self._role_decision(qv)
        p,q,proj=self._candidate_from_levels(side,pair,budget)
        if self.arm=='T':self._probe_submit(t,dict(side='DOWN',price=.75,qty=1.3333333333))
        return self._submit_role(t,side,role,p,q,proj,'FAKE')

class ObserverTests(unittest.TestCase):
    def test_exact_delegation_and_state_preserved(self):
        for arm in ('C','T'):
            with self.subTest(arm=arm),TemporaryDirectory() as tmp:
                plain=Fake(None,arm);obs=make_observer(Fake)(None,arm,Path(tmp)/'trace.jsonl')
                for t in (1,2):
                    plain._reanchor_stale(t);obs._reanchor_stale(t)
                    self.assertEqual(plain._open_one_option(t,{},100),obs._open_one_option(t,{},100))
                self.assertEqual(set(vars(plain)),set(vars(obs)))
                self.assertEqual(vars(plain),vars(obs))
                excluded={'bt','payload','events','times','meta','_receipt_reader','_receipt_ledger'}
                project=lambda x: {k:v for k,v in vars(x).items() if k not in excluded and not k.startswith('_probe_')}
                self.assertEqual(json.dumps(project(plain),sort_keys=True),json.dumps(project(obs),sort_keys=True))
                self.assertEqual(plain.calls,obs.calls)
                self.assertEqual(python_state(plain),python_state(obs))
                self.assertEqual(plain.orders,obs.orders)
                obs.close_trace();rows=[json.loads(x) for x in (Path(tmp)/'trace.jsonl').read_text().splitlines()]
                self.assertEqual(len(rows),2)
                self.assertFalse(rows[0]['after']['reserved'][0]['cancelRequested'])
                self.assertTrue(rows[1]['events'][0]['kind']=='CANCEL')
    def test_actual_pair_results_not_recomputed(self):
        with TemporaryDirectory() as tmp:
            obs=make_observer(Fake)(None,'C',Path(tmp)/'trace.jsonl')
            obs._reanchor_stale(1);obs._open_one_option(1,{},100);obs.close_trace()
            row=json.loads((Path(tmp)/'trace.jsonl').read_text())
            pairs=[x for x in row['events'] if x['kind']=='PAIR_CHECK']
            self.assertEqual([(x['price'],x['accepted']) for x in pairs],[(.6,False),(.4,True)])
            self.assertEqual(len([x for x in obs.calls if x[0]=='pair']),2)
    def test_no_extra_strategy_calls_outside_record(self):
        with TemporaryDirectory() as tmp:
            obs=make_observer(Fake)(None,'C',Path(tmp)/'trace.jsonl')
            self.assertTrue(obs._pair_ok('UP',.4));self.assertEqual(obs.calls,[('pair','UP',.4)])
            obs.close_trace();self.assertEqual((Path(tmp)/'trace.jsonl').stat().st_size,0)
    def test_original_failure_propagates(self):
        class Broken(Fake):
            def _open_one_option(self,*args):raise RuntimeError('original failure')
        with TemporaryDirectory() as tmp:
            obs=make_observer(Broken)(None,'C',Path(tmp)/'trace.jsonl');obs._reanchor_stale(1)
            with self.assertRaisesRegex(RuntimeError,'original failure'):obs._open_one_option(1,{},100)
            obs.close_trace()
    def test_snapshot_does_not_alias_inventory_or_lots(self):
        f=Fake(None,'C');f.un['UP']=[(2.,.4)];snap=python_state(f)
        f.un['UP'][0]=(3.,.5);f.inv['UP']=100
        self.assertEqual(snap['inv']['UP'],0.)
        self.assertEqual(snap['unmatched']['UP']['qty'],2.)

if __name__=='__main__':unittest.main()
