"""Max6 BE consumed-only matched-prefix one-off paid repair probe."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

BUNDLE=Path(__file__).resolve().parent
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_pair_paid_probe_20260910_v1')
PREVIOUS=Path('C:/BTC5M-worker/.tmp/hft244_pair_core_minimal_20260910_v1')
RESULTS=Path('C:/BTC5M-worker/.lan_worker_v1/results')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
REFS={'hft244-pair-core-minimal-20260910-v1':'694bbb59c718a61dc624e207311db6373c593214a0c386c74c3fa2ca612898ae',
      'hft244-pair-only-failure-anatomy-20260910-v1':'b268328f8828117e5141ef67f8237534c579f28ef0098bb4f6d2e88679493da5'}

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def signature(value):return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()

def main():
    if '--child' not in sys.argv:
        helper=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',helper);bound=importlib.util.module_from_spec(spec);spec.loader.exec_module(bound)
        bound.bounded('pair-paid-probe',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(verdict='PRECHECK',attemptedBE=0,historicalLineageBefore=54,rows=[],contrasts=[],
                base='B_PAIR_ONLY_MAX4',freshUsed=0,training=False,promotion=False,fullNetCost='UNRESOLVED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-start,historicalLineageAfter=54+result['attemptedBE'])
        blob=json.dumps(result,indent=2,allow_nan=False).encode();assert len(blob)<1024*1024
        (out/'COMPACT.json').write_bytes(blob)
    try:
        assert not ROOT.exists(),'immutable run root exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,digest in manifest['files'].items():assert sha(BUNDLE/name)==digest,name
        refs=[]
        for job,digest in REFS.items():
            path=RESULTS/job/'COMPACT.json';assert sha(path)==digest;refs.append(json.loads(path.read_text()))
        reference,anatomy=refs;binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
        assert sha(binary)==reference['nativeSha256']
        result.update(inputHashes=manifest['files'],nativeSha256=sha(binary),loadedSourceHashes=reference['loadedSourceHashes'])
        for name,digest in reference['loadedSourceHashes'].items():
            rel=Path(*name.split('.')).with_suffix('.py')
            if not (PREVIOUS/rel).exists():rel=Path(*name.split('.'))/'__init__.py'
            source=PREVIOUS/rel;assert sha(source)==digest,name
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        for name in manifest['files']:
            if name.endswith('.json.xz'):
                dest=ROOT/'tapes'/name;dest.parent.mkdir(exist_ok=True);shutil.copy2(BUNDLE/name,dest)
            elif not name.startswith('run_'):shutil.copy2(BUNDLE/name,ROOT/'tools'/name)
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.hft244_pair_only_anatomy_v1 import receipt_anatomy
        from tools.hft244_pair_paid_probe_v1 import make_sim
        install(minimal.v2.base,binary);Sim=make_sim(minimal)
        assert not any('r2_47' in name or 'repair_overflow_split' in name for name in sys.modules)
        old={r['marketId']:r for r in anatomy['rows']}
        for mid in (1946475,1946683,2023609):
            control=None
            for arm in ('CONTROL','PAID'):
                sim=None;result['attemptedBE']+=1;save()
                try:
                    sim=Sim(ROOT/f'tapes/{mid}.json.xz',arm);output=sim.run_minimal('UP')
                    sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                    assert not sim._receipt_invalid and sim.max_simultaneous_slots<=4
                    receipts=list(sim._receipt_ledger.seen.values());assert len(receipts)<=500
                    for key,o in sim.orders.items():
                        if str(sim.snap(o)['status']).upper() not in minimal.v2.TERMINAL_STATUSES:
                            assert key in sim.slot_key.values(),('unreserved live owner',key)
                    core=dict(output=output,orders=sim.orders,receipts=receipts,native=sim._receipt_ledger.native)
                    row=dict(marketId=mid,arm=arm,correctness=True,signature=signature(core),
                        UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,cost=sim.cost,
                        fills=sim.fills,submits=sim.submits,alternations=output['fillSideAlternations'],
                        reanchors=output['reanchors'],roleFills=output['roleFills'],maxSlots=sim.max_simultaneous_slots,
                        qty=sum(sim.inv.values()),native=sim._receipt_ledger.native,receipts=receipts,
                        risk=sim._probe_risk,mark=sim._probe_mark,probeKey=sim._probe_key,
                        sourceTiming=dict(first=sim.meta['firstReceivedMs'],last=sim.meta['lastReceivedMs'],end=sim.payload['market']['window_end_ms']))
                    row['anatomy']=receipt_anatomy(receipts,row)
                    if arm=='CONTROL':
                        if mid in old:assert row['signature']==old[mid]['signature'],'control changed original trajectory'
                        else:
                            for k in ('UP','DOWN','cost','fills','submits','alternations'):
                                assert abs(row[k]-reference[k])<1e-8,('positive control parity',k)
                            assert row['receipts']==reference['receipts'],'positive control receipt parity'
                        row['priorControlParity']=True;control=row
                    else:
                        assert (row['mark'] is None)==(control['mark'] is None),'state selection parity'
                        if row['mark'] is not None:
                            assert row['mark']==control['mark'],'observable/policy prefix parity'
                        direct=[]
                        if sim._probe_key:
                            order=sim.orders[sim._probe_key];direct=[r for r in receipts if r['order_id']==order['n']]
                            assert sum(r['qty'] for r in direct)<=order['qty']+1e-8
                            paid=sum(r['qty']*(r['price'] if r['side']==1 else 1-r['price']) for r in direct)
                            assert paid<=1.+1e-8,'one-ticket limit breached'
                            row['probeOrder']=dict(order,snapshot=sim.snap(order))
                        else:paid=0.
                        direct_value={s:sum(r['qty'] for r in direct if (r['side']==1)==(s=='UP'))-paid for s in ('UP','DOWN')}
                        delta={s:row[s]-control[s] for s in ('UP','DOWN')}
                        row.update(directReceiptCount=len(direct),directTakerCount=sum(r['maker']==0 for r in direct),directCost=paid)
                        result['contrasts'].append(dict(marketId=mid,prefixParity=True,selected=row['mark'] is not None,
                            directFilled=bool(direct),directTakerExercised=any(r['maker']==0 for r in direct),
                            delta=delta,directEndpoint=direct_value,
                            continuationResidual={s:delta[s]-direct_value[s] for s in delta},
                            activityRatios={k:row[k]/control[k] if control[k] else None for k in ('fills','submits','alternations','cost','qty')},
                            riskRatios={k:row['risk'][k]/control['risk'][k] if control['risk'][k] else None
                                        for k in ('absNetIntegral','grossIntegral','peakAbsNet','peakGross')},
                            activityCollapseFlag=row['fills']==0 or (control['alternations']>0 and row['alternations']==0)))
                    result['rows'].append(row);save()
                finally:
                    if sim is not None:sim.close()
        result['verdict']='PAID_REPAIR_CAUSAL_SMOKE_CAPTURE_SUPPORTED' if any(c['directTakerExercised'] for c in result['contrasts']) else 'NOT_EXERCISED_ACTIVE_TAKER'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=7))
    save();print(json.dumps({k:v for k,v in result.items() if k not in ('rows','inputHashes','loadedSourceHashes','trace')}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)

if __name__=='__main__':main()
