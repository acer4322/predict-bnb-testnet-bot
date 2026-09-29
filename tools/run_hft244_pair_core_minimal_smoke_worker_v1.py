"""One consumed-market accounting integration; no economic promotion."""
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
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_pair_core_minimal_20260910_v1')
FROZEN=Path('C:/BTC5M-worker/.lan_worker_v1/staging/root_family_support_stagea12_20260910_v1')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
STAGE_SHA='da996c4bfd798d91c0286d2c4dae58afc7c75c9a044229e39a40945468b8debb'

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    if '--child' not in sys.argv:
        helper=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',helper)
        bound=importlib.util.module_from_spec(spec);spec.loader.exec_module(bound)
        bound.bounded('pair-core-minimal',[sys.executable,str(Path(__file__).resolve()),'--child'],180)
        return
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);started=time.monotonic()
    result=dict(verdict='PRECHECK',attemptedBE=0,historicalLineageBefore=50,
                cell='B_PAIR_ONLY_MAX4',marketId=2023609,training=False,freshUsed=0,
                promotion=False,fullNetCost='UNRESOLVED',policyParityToHistorical='NOT_CLAIMED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-started,historicalLineageAfter=50+result['attemptedBE'])
        blob=json.dumps(result,indent=2,allow_nan=False).encode()
        assert len(blob)<256*1024
        (out/'COMPACT.json').write_bytes(blob)
    sim=None
    try:
        assert not ROOT.exists(),'immutable run root exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,sha in manifest['files'].items(): assert digest(BUNDLE/name)==sha,name
        assert digest(FROZEN/'STAGE_MANIFEST.json')==STAGE_SHA
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
        assert digest(binary)==NATIVE_SHA
        # Reuse the verified source bundle, not the live checkout. Unused R247
        # source files may exist in it but must never be imported by this runner.
        for rel,sha in json.loads((FROZEN/'STAGE_MANIFEST.json').read_text())['files'].items():
            if not (rel.endswith('.py') or rel=='tapes/2023609.json.xz'): continue
            source=(FROZEN/rel).resolve();assert source.is_relative_to(FROZEN)
            assert source.stat().st_size<=2*1024**2 and digest(source)==sha
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        for name in manifest['files']:
            if name.startswith('run_hft244_'): continue
            shutil.copy2(BUNDLE/name,ROOT/'tools'/name)
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        assert minimal.MinimalPairRoleSim.__bases__==(minimal.v3.RoleSeparatedMultiSlotSim,)
        install(minimal.v2.base,binary)
        loaded={name:digest(Path(module.__file__)) for name,module in list(sys.modules.items())
                if name.startswith(('tools.','src.')) and getattr(module,'__file__',None)}
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in loaded)
        for name,module in list(sys.modules.items()):
            if name in loaded: assert Path(module.__file__).resolve().is_relative_to(ROOT),name
        result.update(loadedSourceHashes=loaded,nativeSha256=NATIVE_SHA)
        result['attemptedBE']=1;save()
        sim=minimal.MinimalPairRoleSim(ROOT/'tapes/2023609.json.xz',4,False)
        row=sim.run_minimal('UP')  # endpoint convention only; no winner selection
        sim._receipt_ledger.reconcile(sim.bt.state_values(0))
        assert sim.serialize_same_side is False and sim.max_slots==4
        assert not sim._receipt_invalid
        assert all(abs(sim.inv[s]-sim._receipt_ledger.inv[s])<1e-8 for s in ('UP','DOWN'))
        assert abs(sim.cost-sim._receipt_ledger.cost)<1e-8
        assert row['maxSimultaneousSlots']<=4
        receipts=list(sim._receipt_ledger.seen.values());assert len(receipts)<=1000
        result.update(verdict='PAIR_CORE_MINIMAL_ACCOUNTING_SMOKE_SUPPORTED',correctness=True,
            mechanismExercised=sim.fills>0,UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,
            cost=sim.cost,qty=sum(sim.inv.values()),fills=sim.fills,submits=sim.submits,
            alternations=row['fillSideAlternations'],twoSided=row['twoSidedMaterialized'],
            maxSlots=row['maxSimultaneousSlots'],roleFills=row['roleFills'],
            roleFillQty=row['roleFillQty'],receipts=receipts,native=sim._receipt_ledger.native,
            reanchors=row['reanchors'],semanticCompletedCycles='NOT_IDENTIFIED_BY_ALTERNATIONS',
            limitations=['One BTC5M integration witness, not ETH cohort replication',
              'Historical role/slot annotations may use limit prices; receipt journal is cash truth',
              'No R247/V8 ownership or economic authority installed',
              'Four role labels do not establish all four passive/active capabilities',
              'Accounting adapter and native repairs can change the old policy trajectory'])
        if sim.fills==0: result['verdict']='NOT_EXERCISED'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=6))
    finally:
        if sim is not None: sim.close()
        save()
    print(json.dumps({k:v for k,v in result.items() if k not in ('receipts','loadedSourceHashes','trace')}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)

if __name__=='__main__':main()
