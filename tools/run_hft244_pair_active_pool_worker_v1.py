"""Six-BE user-requested separate Active pool test, LAN research only."""
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
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_pair_active_pool_20260910_v1')
OLD=Path('C:/BTC5M-worker/.tmp/hft244_pair_confirmed_handoff_20260910_v2')
OLD_BUNDLE=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_pair_confirmed_handoff_20260910_v2')
REFERENCE=Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-pair-confirmed-handoff-20260910-v2/COMPACT.json')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sig(v):return hashlib.sha256(json.dumps(v,sort_keys=True,allow_nan=False).encode()).hexdigest()


def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',p);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.bounded('active-pool',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR'),'LAN research only'
    start=time.monotonic();out=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    result=dict(verdict='PRECHECK',attemptedBE=0,historicalLineageBefore=85,rows=[],comparisons=[],
                freshUsed=0,training=False,promotion=False,fullNetCost='UNRESOLVED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-start,historicalLineageAfter=85+result['attemptedBE'])
        raw=json.dumps(result,separators=(',',':'),allow_nan=False).encode()
        assert len(raw)<=1024**2,'compact limit'
        (out/'COMPACT.json').write_bytes(raw)
    try:
        assert not ROOT.exists(),'immutable root exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,digest in manifest['files'].items():assert sha(BUNDLE/name)==digest,name
        assert sha(REFERENCE)=='064196cb1f21100cca75a6afc523d43713f7c23d97993f45dffdbf6293cf3635'
        ref=json.loads(REFERENCE.read_text());oldrows={(r['marketId'],r['arm']):r for r in ref['rows']}
        assert sha(OLD_BUNDLE/'MANIFEST.json')==manifest['frozenBaseManifestSha256']
        original=json.loads((OLD_BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
        assert sha(binary)==ref['nativeSha256']
        result.update(inputHashes=manifest,referenceSha256=sha(REFERENCE),nativeSha256=sha(binary))
        for name,digest in ref['baseSourceHashes'].items():
            rel=Path(*name.split('.')).with_suffix('.py')
            if not (OLD/rel).exists():rel=Path(*name.split('.'))/'__init__.py'
            assert sha(OLD/rel)==digest,name
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/rel,dest)
        for name,digest in original['files'].items():
            if name.startswith('run_hft244_'):continue
            source=OLD/('tapes' if name.endswith('.json.xz') else 'tools')/name
            assert sha(source)==digest,name
            dest=ROOT/('tapes' if name.endswith('.json.xz') else 'tools')/name
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        shutil.copy2(BUNDLE/'hft244_pair_active_pool_v1.py',ROOT/'tools/hft244_pair_active_pool_v1.py')
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.hft244_pair_confirmed_handoff_v1 import make_sim
        from tools.hft244_pair_active_pool_v1 import make_sim as pool_class,pool_counts
        from tools.hft244_pair_only_anatomy_v1 import receipt_anatomy
        install(minimal.v2.base,binary);Control=make_sim(minimal);Pool=pool_class(minimal)
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in sys.modules)
        for mid in (1830119,1829115,2023609):
            cells={}
            for label,Sim in (('T_ORIGINAL',Control),('X_SEPARATE_ACTIVE_POOL',Pool)):
                sim=None;result['attemptedBE']+=1;save()
                try:
                    sim=Sim(ROOT/f'tapes/{mid}.json.xz','T');output=sim.run_minimal('UP')
                    sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                    assert not sim._receipt_invalid
                    for k,o in sim.orders.items():
                        if sim.snap(o)['status'] not in minimal.v2.TERMINAL_STATUSES:assert k in sim.slot_key.values()
                    receipts=list(sim._receipt_ledger.seen.values());assert len(receipts)<=500
                    core=dict(output=output,orders=sim.orders,receipts=receipts,native=sim._receipt_ledger.native)
                    old=oldrows[mid,'T']
                    assert sim._probe_mark==old['mark'] and sim._probe_ready==old['ready'],'intent/readiness parity'
                    if label=='T_ORIGINAL':
                        assert sig(core)==old['signature'],'control full native parity'
                        assert sim._probe_risk==old['risk'],'control exposure parity'
                        assert sim.max_simultaneous_slots<=4
                    else:
                        pool_counts(sim.slot_key,sim._probe_key)
                        assert sim._probe_pool_peak['passive']<=4 and sim._probe_pool_peak['active']<=1
                        assert sim._probe_pool_peak['total']<=5 and sim.max_simultaneous_slots<=5
                    row=dict(marketId=mid,arm=label,signature=sig(core),correctness=True,
                        UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,cost=sim.cost,
                        qty=sum(sim.inv.values()),fills=sim.fills,submits=sim.submits,
                        alternations=output['fillSideAlternations'],maxPhysicalSlots=sim.max_simultaneous_slots,
                        poolPeak=getattr(sim,'_probe_pool_peak',None),
                        extraPassiveAdmissions=getattr(sim,'_probe_pool_extra_admissions',0),
                        roleFills=output['roleFills'],risk=sim._probe_risk,mark=sim._probe_mark,ready=sim._probe_ready,
                        stage=sim._probe_stage,events=sim._probe_events,crossBlocks=sim._probe_cross_blocks,
                        receipts=receipts,native=sim._receipt_ledger.native)
                    row['anatomy']=receipt_anatomy(receipts,row)
                    direct=[]
                    if sim._probe_key:
                        order=sim.orders[sim._probe_key]
                        direct=[r for r in receipts if r['order_id']==order['n']]
                    paid=sum(r['qty']*(r['price'] if r['side']==1 else 1-r['price']) for r in direct)
                    assert paid<=1+1e-8
                    assert sum(r['qty'] for r in direct)<=sim._probe_mark['option']['qty']+1e-8
                    row.update(directReceipts=direct,directPayment=paid,takerExercised=any(r['maker']==0 for r in direct))
                    cells[label]=row;result['rows'].append(row)
                    if label=='X_SEPARATE_ACTIVE_POOL':
                        t=cells['T_ORIGINAL'];delta=lambda other:{s:row[s]-other[s] for s in ('UP','DOWN')}
                        result['comparisons'].append(dict(marketId=mid,poolExercised=row['extraPassiveAdmissions']>0,
                            X_minus_T=delta(t),X_minus_C=delta(oldrows[mid,'C']),X_minus_A=delta(oldrows[mid,'A']),
                            ratiosX_T={k:row[k]/t[k] if t[k] else None for k in ('fills','alternations','submits','cost','qty')},
                            riskRatiosX_T={k:row['risk'][k]/t['risk'][k] if t['risk'][k] else None for k in ('absNetIntegral','grossIntegral','peakAbsNet','peakGross')},
                            minEndpointDelta=row['risk']['minEndpoint']-t['risk']['minEndpoint'],
                            directReceiptParity=direct==t['directReceipts'],
                            hardCollapse=row['fills']==0 or (t['alternations']>0 and row['alternations']==0)))
                    save()
                except Exception:
                    if sim is not None:result['lastPythonOnly']=dict(marketId=mid,arm=label,
                        mark=sim._probe_mark,stage=sim._probe_stage,events=sim._probe_events)
                    raise
                finally:
                    if sim is not None:sim.close()
        result['verdict']='SEPARATE_ACTIVE_POOL_CAPTURE_SUPPORTED' if any(c['poolExercised'] for c in result['comparisons']) else 'NOT_EXERCISED'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=8))
    save();print(json.dumps({k:v for k,v in result.items() if k not in ('rows','comparisons','inputHashes','trace')}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)


if __name__=='__main__':main()
