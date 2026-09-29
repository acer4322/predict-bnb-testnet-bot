"""Component integration using saved native receipts; no HFT, tape or model import."""
import ast
from collections import Counter,deque
import copy
import json
from pathlib import Path
from types import SimpleNamespace,MethodType
import unittest

from tools.hft244_receipt_adapter_v1 import Ledger
from tools.hft244_research_owner_accounting_v1 import physical_process,role_process,strict_advance_to,install_preview_codec

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/research/lan_worker_returns/hft244-receipts-v4-20260910-v1/v4-receipt-contract.json'


def method(path,cls,name,scope):
    tree=ast.parse(path.read_text());c=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
    f=next(n for n in c.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[f],type_ignores=[]),str(path),'exec'),scope);return scope[name]


def state(rows,repair_quota=None):
    sim=SimpleNamespace();sim.orders={};last={}
    for r in rows:
        side='UP' if r['side']==1 else 'DOWN';key=f'{side}_{r["order_id"]}'
        sim.orders.setdefault(key,dict(n=r['order_id'],side=side,price=.76 if r['side']==1 else .76,qty=r['cumulative_qty']+r['leaves_qty'],cum=0.))
        last[r['order_id']]=r
    expected=Ledger();expected.consume(rows)
    native=SimpleNamespace(**expected.native)
    sim.bt=SimpleNamespace(state_values=lambda asset:native)
    class Reader:
        def __init__(self):self.rows=copy.deepcopy(rows)
        def peek(self):return copy.deepcopy(self.rows)
        def ack(self,batch):self.rows=[]
    sim._receipt_reader=Reader();sim._receipt_ledger=Ledger();sim._receipt_invalid=False
    sim.inv={'UP':0.,'DOWN':0.};sim.cost=0.;sim.sideCost={'UP':0.,'DOWN':0.};sim.un={'UP':deque(),'DOWN':deque()}
    sim.pairReserve=0.;sim.pairedQty=0.;sim.fillHist=deque();sim.fills=0
    sim.snap=lambda o:dict(cumExecQty=last[o['n']]['cumulative_qty'],status='CANCELED')
    record=method(ROOT/'tools/run_eth_dagger60_smoke_v1.py','Sim','record_fill',{'EPS':1e-9})
    sim.record_fill=MethodType(record,sim)
    sim._receipt_v2_process=physical_process
    sim.scopeSide='DOWN' if rows[0]['side']==1 else 'UP';sim.scopeGeneration=7
    sim._physical_floor=lambda:min(sim.inv.values())-sim.cost
    # Scope transition/reservation methods are deliberately stubs: these tests
    # establish the physical->role arithmetic seam, NOT full R2.47 behavior.
    sim._unmatched_scope_side=lambda:sim.scopeSide;sim._sync_scope=lambda *a:None;sim._audit_reservation=lambda:None
    sim.key_role={k:'ECONOMIC_CORE' for k in sim.orders};sim.key_scope_gen={k:7 for k in sim.orders}
    sim.keyRepairQuotaRemaining={k:(repair_quota if repair_quota is not None else last[o['n']]['cumulative_qty']) for k,o in sim.orders.items()}
    sim.keyOverflowQtyRemaining={k:10. for k in sim.orders};sim.role_fills=Counter();sim.role_fill_qty=Counter()
    for name in ['unauthorizedOverflowQty','totalRepairAllocated','totalOverflowRealized','totalOverflowRiskConsumed','scopeRiskCreditConsumed','scopeRepairProgressClocks','totalRepairProgressClocks','totalRepairCreditValue','scopeRiskCreditTotal']:setattr(sim,name,0.)
    sim.expand_consumed_keys=set();sim.splitEvents=[];sim.slot_history=[];sim.scope_credit_events=[]
    return sim


class TestOwnerAccounting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.rows=json.loads(DATA.read_text())['rows']
    def saved(self,kind,side):return next(r for r in self.rows if r['kind']==kind and r['side']==side)['receipts']
    def test_two_price_quota_boundary_both_sides(self):
        for side in ['BUY','SELL']:
            s=state(self.saved('TAKER_TWO_PRICES',side),.8);role_process(s,1700)
            self.assertAlmostEqual(s.cost,1.02);self.assertAlmostEqual(s.totalRepairAllocated,.8)
            self.assertAlmostEqual(s.totalOverflowRealized,.55);self.assertAlmostEqual(s.totalRepairCreditValue,.198)
            self.assertAlmostEqual(s.totalOverflowRiskConsumed,.418);self.assertEqual(s.fills,1);self.assertEqual(len(s.fillHist),1)
            self.assertEqual(len(s.splitEvents),2);self.assertEqual([e['price'] for e in s.splitEvents],[.75,.76])
            self.assertEqual(next(iter(s.orders.values()))['price'],.76)
            before=(s.cost,s.totalRepairAllocated,s.totalRepairCreditValue,len(s.splitEvents),len(s.fillHist))
            role_process(s,1700);self.assertEqual(before,(s.cost,s.totalRepairAllocated,s.totalRepairCreditValue,len(s.splitEvents),len(s.fillHist)))
    def test_active_passive_cancel_boundary(self):
        for side in ['BUY','SELL']:
            s=state(self.saved('TAKE_PASSIVE_CANCEL',side),.7);role_process(s,2900)
            self.assertAlmostEqual(s.cost,.64);self.assertAlmostEqual(s.totalRepairCreditValue,.174)
            self.assertAlmostEqual(s.totalOverflowRiskConsumed,.114)
            self.assertEqual([e['nativeMaker'] for e in s.splitEvents],[0,1]);self.assertEqual(s.fills,1)
            self.assertTrue(all(o['status']=='CANCELED' for o in s.orders.values()))
    def test_same_price_preserves_legacy_totals_and_count_clock(self):
        for side in ['BUY','SELL']:
            receipts=self.saved('QUEUE_AHEAD',side);a=state(receipts);b=state(receipts)
            for sim in [a,b]:
                for o in sim.orders.values():o['price']=.74
            old=method(ROOT/'tools/run_eth_role_separated_multislot_v8_repair_overflow_split_smoke.py','RepairOverflowSplitSim','process',{'EPS':1e-9,'REPAIR_ROLES':{'ECONOMIC_CORE','SATELLITE_REPAIR'},'v2':SimpleNamespace(TargetGroundedDistinctSlotSim=SimpleNamespace(process=physical_process))})
            old(a,2800);role_process(b,2800)
            for key in ['cost','fills','totalRepairAllocated','totalOverflowRealized','totalOverflowRiskConsumed','totalRepairCreditValue']:self.assertAlmostEqual(getattr(a,key),getattr(b,key),msg=key)
            self.assertEqual(a.role_fills,b.role_fills);self.assertEqual(len(b.fillHist),2)
            self.assertEqual(len(b._receipt_delta_rows),3)
    def test_unknown_owner_is_rejected_before_mutation(self):
        s=state(self.saved('TAKER_TWO_PRICES','BUY'));s.orders={}
        with self.assertRaisesRegex(AssertionError,'no policy owner'):physical_process(s,1700)
        self.assertEqual(s.cost,0);self.assertTrue(s._receipt_invalid)
    def test_native_id_alias_is_rejected(self):
        s=state(self.saved('TAKER_TWO_PRICES','BUY'));s.orders['alias']=dict(next(iter(s.orders.values())))
        with self.assertRaisesRegex(AssertionError,'ambiguous'):physical_process(s,1700)
        self.assertEqual(s.cost,0)
    def test_sequence_gap_is_rejected(self):
        s=state(self.saved('TAKER_TWO_PRICES','BUY'));s._receipt_reader.rows.pop(0)
        with self.assertRaisesRegex(AssertionError,'sequence gap'):physical_process(s,1700)
        self.assertEqual(s.cost,0)
    def test_advance_error_not_treated_as_eof(self):
        for rc in [13,14,100]:
            bt=SimpleNamespace(current_timestamp=0,elapse=lambda duration,rc=rc:rc)
            with self.assertRaisesRegex(RuntimeError,'NATIVE_EXECUTION_INVALID'):strict_advance_to(bt,1)
        self.assertFalse(strict_advance_to(SimpleNamespace(current_timestamp=0,elapse=lambda duration:1),1))
    def test_preview_codec_captures_ledger_and_blocks_native_mutation(self):
        import math
        path=ROOT/'tools/run_root_dual_legal_label_smoke_v1.py';tree=ast.parse(path.read_text())
        chosen=[n for n in tree.body if (isinstance(n,ast.FunctionDef) and n.name=='policy_state') or (isinstance(n,ast.ClassDef) and n.name=='ReadOnlyBackend')]
        scope={'INPUTS':{'bt','events','times','raw','payload','meta','execModel'}}
        exec(compile(ast.Module(body=chosen,type_ignores=[]),str(path),'exec'),scope)
        utilpath=ROOT/'tools/run_root_btc5m_source_smoke_v1.py';utree=ast.parse(utilpath.read_text())
        cscope={'deque':deque,'Path':Path,'math':math,'json':json}
        clean=next(n for n in utree.body if isinstance(n,ast.FunctionDef) and n.name=='clean')
        exec(compile(ast.Module(body=[clean],type_ignores=[]),str(utilpath),'exec'),cscope)
        class Policy:pass
        sim=Policy();sim.inv={'UP':0.,'DOWN':0.};sim.bt=SimpleNamespace(state_values=lambda a:None)
        sim._receipt_ledger=Ledger();sim._receipt_v2_process=physical_process
        sim._receipt_reader=SimpleNamespace(asset=0,capacity=4096,peek=lambda:[])
        with self.assertRaisesRegex(TypeError,'Ledger'):cscope['clean'](scope['policy_state'](sim))
        lab=SimpleNamespace(INPUTS=scope['INPUTS'],policy_state=scope['policy_state'],ReadOnlyBackend=scope['ReadOnlyBackend'])
        install_preview_codec(lab,physical_process)
        original=cscope['clean'](lab.policy_state(sim));clone=lab.preview_copy(sim)
        self.assertEqual(original,cscope['clean'](lab.policy_state(clone)))
        with self.assertRaisesRegex(RuntimeError,'preview forbids'):clone._receipt_reader.ack([])
        with self.assertRaisesRegex(RuntimeError,'preview forbids'):clone._receipt_reader.peek()
        with self.assertRaisesRegex(RuntimeError,'preview forbids'):clone.bt.elapse(1)
        clone._receipt_ledger.cost=123
        self.assertEqual(sim._receipt_ledger.cost,0)
        self.assertNotEqual(original,cscope['clean'](lab.policy_state(clone)))
        self.assertEqual(original,cscope['clean'](lab.policy_state(sim)))


if __name__=='__main__':unittest.main()
