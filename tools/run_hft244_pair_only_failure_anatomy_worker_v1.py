"""Two consumed ETH failure cases + one inert observer parity, max3 BE."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from collections import Counter

BUNDLE=Path(__file__).resolve().parent
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_pair_only_failure_anatomy_20260910_v1')
PREVIOUS=Path('C:/BTC5M-worker/.tmp/hft244_pair_core_minimal_20260910_v1')
REFERENCE=Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-pair-core-minimal-20260910-v1/COMPACT.json')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def signature(value):return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()

def main():
    if '--child' not in sys.argv:
        helper=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',helper);bound=importlib.util.module_from_spec(spec);spec.loader.exec_module(bound)
        bound.bounded('pair-only-anatomy',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(verdict='PRECHECK',attemptedBE=0,historicalLineageBefore=51,rows=[],
                policy='B_PAIR_ONLY_MAX4',freshUsed=0,training=False,promotion=False,fullNetCost='UNRESOLVED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-start,historicalLineageAfter=51+result['attemptedBE'])
        blob=json.dumps(result,indent=2,allow_nan=False).encode();assert len(blob)<512*1024
        (out/'COMPACT.json').write_bytes(blob)
    try:
        assert not ROOT.exists(),'immutable run root exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,sha in manifest['files'].items():assert digest(BUNDLE/name)==sha,name
        assert digest(REFERENCE)=='694bbb59c718a61dc624e207311db6373c593214a0c386c74c3fa2ca612898ae'
        ref=json.loads(REFERENCE.read_text())
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert digest(binary)==ref['nativeSha256']
        result.update(inputHashes=manifest['files'],nativeSha256=digest(binary),
                      loadedSourceHashes=ref['loadedSourceHashes'])
        for name,sha in ref['loadedSourceHashes'].items():
            rel=Path(*name.split('.')).with_suffix('.py')
            if not (PREVIOUS/rel).exists():rel=Path(*name.split('.'))/'__init__.py'
            source=PREVIOUS/rel;assert digest(source)==sha,name
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        for name in manifest['files']:
            if name.endswith('.json.xz'):
                dest=ROOT/'tapes'/name;dest.parent.mkdir(exist_ok=True);shutil.copy2(BUNDLE/name,dest)
            elif name=='hft244_pair_only_anatomy_v1.py':shutil.copy2(BUNDLE/name,ROOT/'tools'/name)
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.hft244_pair_only_anatomy_v1 import quote_frontier,receipt_anatomy
        install(minimal.v2.base,binary)
        assert not any('r2_47' in name or 'repair_overflow_split' in name for name in sys.modules)
        class Observed(minimal.MinimalPairRoleSim):
            def __init__(self,tape,enabled):
                self.observe=enabled;self.counts=Counter();self.seams=[];self.last_seam=None
                super().__init__(tape,4,False)
            def _open_one_option(self,t,qv,end):
                if self.observe and end-t>minimal.v2.NO_NEW_EXPOSURE_MS:
                    side,role,_,_=self._role_decision(qv)
                    row=quote_frontier(self.inv,self.un,qv,side,role)
                    self.counts['preFenceRoleDecision']+=1
                    if role in ('ECONOMIC_CORE','SATELLITE_REPAIR'):
                        self.counts['repairRoleDecision']+=1
                        self.counts['repairAskPairPass' if row['askPairPass'] else 'repairAskPairFail']+=1
                        if row['oppositeUnmatchedQty']>1e-9 and not row['askPairPass']:
                            row.update(t=int(t),UP=self.inv['UP']-self.cost,DOWN=self.inv['DOWN']-self.cost,
                                       liveKeys=list(self.slot_key.values()))
                            self.last_seam=row
                            if len(self.seams)<8:self.seams.append(row)
                return super()._open_one_option(t,qv,end)
        for mid,enabled in [(1946475,False),(1946475,True),(1946683,True)]:
            sim=None;result['attemptedBE']+=1;save()
            try:
                sim=Observed(ROOT/f'tapes/{mid}.json.xz',enabled)
                output=sim.run_minimal('UP')
                sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                assert not sim._receipt_invalid and sim.max_simultaneous_slots<=4
                assert abs(sim.cost-sim._receipt_ledger.cost)<1e-8
                assert all(abs(sim.inv[s]-sim._receipt_ledger.inv[s])<1e-8 for s in ('UP','DOWN'))
                receipts=list(sim._receipt_ledger.seen.values());assert len(receipts)<=500
                core=dict(output=output,orders=sim.orders,receipts=receipts,native=sim._receipt_ledger.native)
                row=dict(marketId=mid,observer=enabled,correctness=True,signature=signature(core),
                         UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,cost=sim.cost,
                         fills=sim.fills,submits=sim.submits,alternations=output['fillSideAlternations'],
                         maxSlots=sim.max_simultaneous_slots,native=sim._receipt_ledger.native,
                         roleFills=output['roleFills'],receipts=receipts,counts=dict(sim.counts),
                         seams=sim.seams,lastSeam=sim.last_seam,veto=output['vetoCounts'],
                         sourceTiming=dict(first=sim.meta['firstReceivedMs'],last=sim.meta['lastReceivedMs'],
                                           end=sim.payload['market']['window_end_ms']))
                row['anatomy']=receipt_anatomy(receipts,row)
                result['rows'].append(row);save()
                if len(result['rows'])==2:
                    assert row['signature']==result['rows'][0]['signature'],'observer altered whole behavior/ledger'
                    result['observerParity']=True
                if sim.fills==0:
                    result['verdict']='NOT_EXERCISED';break
            finally:
                if sim is not None:sim.close()
        if result['verdict']=='PRECHECK':result['verdict']='PAIR_ONLY_FAILURE_ANATOMY_CAPTURE_SUPPORTED'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=6))
    save()
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','trace','inputHashes','loadedSourceHashes')}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)

if __name__=='__main__':main()
