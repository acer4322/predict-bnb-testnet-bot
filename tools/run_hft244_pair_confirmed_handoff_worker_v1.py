"""Nine-BE max cancel-control/confirmed-active handoff smoke; LAN only."""
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
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_pair_confirmed_handoff_20260910_v2')
PREVIOUS=Path('C:/BTC5M-worker/.tmp/hft244_pair_core_minimal_20260910_v1')
RESULTS=Path('C:/BTC5M-worker/.lan_worker_v1/results')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sig(v):return hashlib.sha256(json.dumps(v,sort_keys=True,allow_nan=False).encode()).hexdigest()

def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',p);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
        b.bounded('confirmed-handoff',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(verdict='PRECHECK',attemptedBE=0,historicalLineageBefore=67,rows=[],contrasts=[],
                freshUsed=0,training=False,promotion=False,fullNetCost='UNRESOLVED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-start,historicalLineageAfter=67+result['attemptedBE'])
        blob=json.dumps(result,indent=2,allow_nan=False).encode();assert len(blob)<2*1024**2
        (out/'COMPACT.json').write_bytes(blob)
    try:
        assert not ROOT.exists(),'immutable root exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,digest in manifest['files'].items():assert sha(BUNDLE/name)==digest,name
        p=RESULTS/'hft244-pair-core-minimal-20260910-v1/COMPACT.json'
        assert sha(p)=='694bbb59c718a61dc624e207311db6373c593214a0c386c74c3fa2ca612898ae';ref=json.loads(p.read_text())
        p=RESULTS/'hft244-detail-v2-reentry-20260910-v1/COMPACT.json'
        assert sha(p)=='e73cd6b11987163b4a9d50602d4c1944ce1e34b015c6d6076d708770c114deaa';prior=json.loads(p.read_text())
        old={r['marketId']:r for r in prior['rows'] if r['cell']=='A_PAIR_ONLY_BASELINE'}
        v2={r['marketId']:r for r in prior['rows'] if r['cell']=='B_SAME_INTENT_FRONTIER_QUEUE_V2'}
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==ref['nativeSha256']
        result.update(inputHashes=manifest['files'],nativeSha256=sha(binary),baseSourceHashes=ref['loadedSourceHashes'])
        for name,digest in ref['loadedSourceHashes'].items():
            rel=Path(*name.split('.')).with_suffix('.py')
            if not (PREVIOUS/rel).exists():rel=Path(*name.split('.'))/'__init__.py'
            source=PREVIOUS/rel;assert sha(source)==digest,name
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        for name in manifest['files']:
            if name==Path(__file__).name:continue
            dest=ROOT/('tapes' if name.endswith('.json.xz') else 'tools')/name
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BUNDLE/name,dest)
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.hft244_pair_confirmed_handoff_v1 import make_sim
        from tools.hft244_pair_only_anatomy_v1 import receipt_anatomy
        install(minimal.v2.base,binary);Sim=make_sim(minimal)
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in sys.modules)
        for mid in (1830119,1829115,2023609):
            cells={}
            for arm in ('A','C','T'):
                sim=None;result['attemptedBE']+=1;save()
                try:
                    sim=Sim(ROOT/f'tapes/{mid}.json.xz',arm);output=sim.run_minimal('UP')
                    sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                    assert not sim._receipt_invalid and sim.max_simultaneous_slots<=4
                    receipts=list(sim._receipt_ledger.seen.values());assert len(receipts)<=500
                    for k,o in sim.orders.items():
                        if sim.snap(o)['status'] not in minimal.v2.TERMINAL_STATUSES:assert k in sim.slot_key.values()
                    core=dict(output=output,orders=sim.orders,receipts=receipts,native=sim._receipt_ledger.native)
                    row=dict(marketId=mid,arm=arm,signature=sig(core),UP=sim.inv['UP']-sim.cost,
                        DOWN=sim.inv['DOWN']-sim.cost,cost=sim.cost,fills=sim.fills,submits=sim.submits,
                        alternations=output['fillSideAlternations'],qty=sum(sim.inv.values()),maxSlots=sim.max_simultaneous_slots,
                        roleFills=output['roleFills'],correctness=True,receipts=receipts,native=sim._receipt_ledger.native,
                        risk=sim._probe_risk,mark=sim._probe_mark,ready=sim._probe_ready,stage=sim._probe_stage,
                        events=sim._probe_events,holdClocks=sim._probe_hold_clocks,crossBlocks=sim._probe_cross_blocks,
                        potentialCrosses=sim._probe_cross_seen)
                    row['anatomy']=receipt_anatomy(receipts,row)
                    if arm=='A':assert row['signature']==old[mid]['signature'],'baseline parity'
                    else:assert row['mark']==cells['A']['mark'],'selection prefix/intent parity'
                    if arm=='T':
                        assert row['ready']==cells['C']['ready'],'C/T readiness prefix parity'
                        direct=[];paid=0.
                        if sim._probe_key:
                            o=sim.orders[sim._probe_key];direct=[r for r in receipts if r['order_id']==o['n']]
                            paid=sum(r['qty']*(r['price'] if r['side']==1 else 1-r['price']) for r in direct)
                            assert paid<=1.+1e-8 and sum(r['qty'] for r in direct)<=o['qty']+1e-8
                            row['activeOrder']=dict(o,snapshot=sim.snap(o))
                        else:assert row['signature']==cells['C']['signature'],'uncommitted C/T policy drift'
                        direct_seq={r['sequence'] for r in direct}
                        row.update(directReceipts=direct,directPayment=paid,
                                   directMatchedAtFill=sum(p['qty'] for p in row['anatomy']['pairs'] if p['closeSeq'] in direct_seq))
                        delta=lambda other:{s:row[s]-other[s] for s in ('UP','DOWN')}
                        direct_value={s:sum(r['qty'] for r in direct if (r['side']==1)==(s=='UP'))-paid for s in ('UP','DOWN')}
                        result['contrasts'].append(dict(marketId=mid,selected=row['mark'] is not None,
                            committed=sim._probe_key is not None,takerExercised=any(r['maker']==0 for r in direct),
                            C_minus_A={s:cells['C'][s]-cells['A'][s] for s in ('UP','DOWN')},
                            T_minus_C=delta(cells['C']),T_minus_A=delta(cells['A']),T_minus_V2=delta(v2[mid]),
                            directEndpoint=direct_value,
                            continuationResidual={s:row[s]-cells['C'][s]-direct_value[s] for s in ('UP','DOWN')},
                            ratiosT_A={k:row[k]/cells['A'][k] if cells['A'][k] else None for k in ('fills','submits','alternations','cost','qty')},
                            ratiosT_C={k:row[k]/cells['C'][k] if cells['C'][k] else None for k in ('fills','submits','alternations','cost','qty')},
                            hardCollapse=row['fills']==0 or (cells['A']['alternations']>0 and row['alternations']==0)))
                    cells[arm]=row;result['rows'].append(row);save()
                except Exception:
                    if sim is not None:
                        result['lastPythonOnly']=dict(marketId=mid,arm=arm,mark=sim._probe_mark,stage=sim._probe_stage,events=sim._probe_events)
                    raise
                finally:
                    if sim is not None:sim.close()
        result['verdict']='CONFIRMED_HANDOFF_ACTIVE_CAPTURE_SUPPORTED' if any(c['takerExercised'] for c in result['contrasts']) else 'NOT_EXERCISED_ACTIVE_TAKER'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=7))
    save();print(json.dumps({k:v for k,v in result.items() if k not in ('rows','contrasts','inputHashes','baseSourceHashes','lastPythonOnly','trace')}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)

if __name__=='__main__':main()
