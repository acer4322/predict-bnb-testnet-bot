"""Bounded corrected-native requalification of existing V2, max10 market BE."""
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
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_detail_v2_reentry_20260910_v1')
PREVIOUS=Path('C:/BTC5M-worker/.tmp/hft244_pair_core_minimal_20260910_v1')
REF=Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-pair-core-minimal-20260910-v1/COMPACT.json')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sig(v):return hashlib.sha256(json.dumps(v,sort_keys=True,allow_nan=False).encode()).hexdigest()

def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',p);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
        b.bounded('detail-v2-reentry',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(verdict='PRECHECK',attemptedBE=0,historicalLineageBefore=56,rows=[],contrasts=[],
                freshUsed=0,training=False,promotion=False,fullNetCost='UNRESOLVED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-start,historicalLineageAfter=56+result['attemptedBE'])
        blob=json.dumps(result,indent=2,allow_nan=False).encode();assert len(blob)<2*1024**2
        (out/'COMPACT.json').write_bytes(blob)
    try:
        assert not ROOT.exists(),'immutable root exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,digest in manifest['files'].items():assert sha(BUNDLE/name)==digest,name
        assert sha(REF)=='694bbb59c718a61dc624e207311db6373c593214a0c386c74c3fa2ca612898ae'
        ref=json.loads(REF.read_text());binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
        assert sha(binary)==ref['nativeSha256']
        result.update(inputHashes=manifest['files'],nativeSha256=sha(binary),baseSourceHashes=ref['loadedSourceHashes'])
        for name,digest in ref['loadedSourceHashes'].items():
            rel=Path(*name.split('.')).with_suffix('.py')
            if not (PREVIOUS/rel).exists():rel=Path(*name.split('.'))/'__init__.py'
            source=PREVIOUS/rel;assert sha(source)==digest,name
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        for name in manifest['files']:
            if name==Path(__file__).name:continue
            if name.endswith('.json.xz'):rel=Path('tapes')/name
            elif name.startswith('detail_intelligence_'):rel=Path('tools/eth_repair_modular')/name
            else:rel=Path('tools')/name
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BUNDLE/name,dest)
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools import run_gpt6_detail_intelligence_v2_external as v2
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.hft244_detail_v2_receipts_v1 import make_sim,first_divergence
        from tools.hft244_pair_only_anatomy_v1 import receipt_anatomy
        install(minimal.v2.base,binary);Sim=make_sim(minimal,v2)
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in sys.modules)
        plain=None
        for mid in (1830119,1829115,2023609):
            traces={};by_cell={}
            for cell in (('PLAIN',)+v2.CELLS if mid==1830119 else v2.CELLS):
                sim=None;result['attemptedBE']+=1;save()
                try:
                    tape=ROOT/f'tapes/{mid}.json.xz'
                    sim=minimal.MinimalPairRoleSim(tape,4,False) if cell=='PLAIN' else Sim(tape,cell)
                    output=sim.run_minimal('UP');sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                    assert not sim._receipt_invalid and sim.max_simultaneous_slots<=4
                    receipts=list(sim._receipt_ledger.seen.values());assert len(receipts)<=500
                    core=dict(output=output,orders=sim.orders,receipts=receipts,native=sim._receipt_ledger.native)
                    row=dict(marketId=mid,cell=cell,signature=sig(core),UP=sim.inv['UP']-sim.cost,
                        DOWN=sim.inv['DOWN']-sim.cost,cost=sim.cost,fills=sim.fills,submits=sim.submits,
                        alternations=output['fillSideAlternations'],reanchors=output['reanchors'],
                        roleFills=output['roleFills'],maxSlots=sim.max_simultaneous_slots,correctness=True,
                        receipts=receipts,native=sim._receipt_ledger.native)
                    row['anatomy']=receipt_anatomy(receipts,row)
                    if cell=='PLAIN':plain=row['signature']
                    else:
                        traces[cell]=sim.detail_stream.rows
                        row.update(detailCounters=dict(sim.detail_counter),syntheticOrders=len(sim.v2_synthetic_keys),
                            syntheticFillQty=sum(x['confirmedQty'] for k,o in sim.detail_outcomes.items() if k in sim.v2_synthetic_keys for x in o['fills']),
                            syntheticRepairQty=sum(x['matchedRepairQty'] for k,o in sim.detail_outcomes.items() if k in sim.v2_synthetic_keys for x in o['fills']),
                            matchedQty=sum(x['matchedRepairQty'] for x in sim.detail_fill_accounting))
                        assert abs(row['matchedQty']-row['anatomy']['pairedQty'])<1e-8
                        assert len(sim.detail_fill_accounting)==len(receipts)
                        if cell==v2.CELLS[0]:
                            if mid==1830119:
                                assert row['signature']==plain,'observer changed baseline';result['observerParity']=True
                            if mid==2023609:
                                assert row['receipts']==ref['receipts']
                                for k in ('UP','DOWN','cost','fills','submits','alternations'):assert abs(row[k]-ref[k])<1e-8
                                result['positiveBaselineParity']=True
                        else:
                            base=by_cell[v2.CELLS[0]]
                            divergence=first_divergence(traces[v2.CELLS[0]],traces[cell])
                            if divergence:assert divergence['samePreActionState'],'first divergence not aligned'
                            result['contrasts'].append(dict(marketId=mid,cell=cell,firstDivergence=divergence,
                                delta={k:row[k]-base[k] for k in ('UP','DOWN','fills','submits','alternations','cost')},
                                syntheticFillQty=row['syntheticFillQty'],syntheticRepairQty=row['syntheticRepairQty']))
                    by_cell[cell]=row;result['rows'].append(row);save()
                except Exception:
                    if sim is not None and hasattr(sim,'detail_stream'):
                        # Python-owned pre-error trace only. Never inspect failed native state.
                        result['lastRecordedDecision']=sim.detail_stream.rows[-1] if sim.detail_stream.rows else None
                    raise
                finally:
                    if sim is not None:sim.close()
        result['verdict']='V2_CORRECTED_NATIVE_REQUALIFICATION_CAPTURE_SUPPORTED'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=7))
    save();print(json.dumps({k:v for k,v in result.items() if k not in ('rows','contrasts','inputHashes','baseSourceHashes','lastRecordedDecision','trace')}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)

if __name__=='__main__':main()
