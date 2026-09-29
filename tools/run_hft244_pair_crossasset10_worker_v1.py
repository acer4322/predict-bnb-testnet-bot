"""Frozen cross-asset development replication; no fitting; HFT on LAN only."""
import collections
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
import zipfile

BUNDLE = Path(__file__).resolve().parent
OLD = Path('C:/BTC5M-worker/.tmp/hft244_pair_confirmed_handoff_20260910_v2')
OLD_BUNDLE = Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_pair_confirmed_handoff_20260910_v2')
REFERENCE = Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-pair-confirmed-handoff-20260910-v2/COMPACT.json')
BACKEND = Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
NATIVE_SHA = '7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
REF_SHA = '064196cb1f21100cca75a6afc523d43713f7c23d97993f45dffdbf6293cf3635'
POOL_SHA = '3b87c8e1dcdea362ddc2f08de589fe65787c77b2cd48830bfbb2464e2d990242'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sig(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,allow_nan=False).encode()).hexdigest()


def independent_audit(receipts, native, up_endpoint, down_endpoint, cost):
    """Reconstruct every execution rather than delta cumulative * last price."""
    up=down=cash=0.0
    cumulative=collections.defaultdict(float)
    max_cum=0.0
    for i,r in enumerate(receipts,1):
        assert r['sequence']==i and r['qty']>0 and r['leaves_qty']>=-1e-9
        assert r['side'] in (1,-1) and r['receive_ts']>=r['exchange_ts']
        key=(r['order_id'],r['generation']);cumulative[key]+=r['qty']
        max_cum=max(max_cum,abs(cumulative[key]-r['cumulative_qty']))
        if r['side']==1:up+=r['qty']
        else:down+=r['qty']
        cash+=r['qty']*(r['price'] if r['side']==1 else 1-r['price'])
    expected=dict(position=up-down,balance=sum(-r['side']*r['qty']*r['price'] for r in receipts),
        trading_volume=up+down,trading_value=sum(r['qty']*r['price'] for r in receipts),
        num_trades=len(receipts),fee=sum(r['fee'] for r in receipts))
    errors={k:abs(v-native[k]) for k,v in expected.items()}
    errors.update(UP=abs(up-cash-up_endpoint),DOWN=abs(down-cash-down_endpoint),cost=abs(cash-cost),cumulative=max_cum)
    assert max(errors.values())<1e-8,errors
    return dict(maxError=max(errors.values()),receiptCount=len(receipts),
        partialReceipts=sum(r['leaves_qty']>1e-9 for r in receipts),
        multiReceiptOwners=sum(n>1 for n in collections.Counter((r['order_id'],r['generation']) for r in receipts).values()),
        nativeFee=expected['fee'])


def main():
    asset=sys.argv[1];assert asset in ('BTC','ETH')
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        sp=importlib.util.spec_from_file_location('bound',p);mod=importlib.util.module_from_spec(sp);sp.loader.exec_module(mod)
        mod.bounded('crossasset-'+asset.lower(),[sys.executable,str(Path(__file__).resolve()),asset,'--child'],180)
        return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR'),'LAN only'
    root=Path('C:/BTC5M-worker/.tmp/hft244_pair_crossasset10_20260910_v1_'+asset.lower())
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(version='PAIR_CORE_CROSSASSET10_V1',asset=asset,verdict='PRECHECK',attemptedBE=0,
        rows=[],comparisons=[],newTraining=0,freshUsed=0,promotion=False,fullNetCost='UNRESOLVED',
        cohortClass='CONSUMED_DEVELOPMENT_CROSS_ASSET_REPLICATION',policyChangedFromFrozenArms=False)
    def save():
        result['elapsedSeconds']=time.monotonic()-start
        raw=json.dumps(result,separators=(',',':'),allow_nan=False).encode();assert len(raw)<=2*1024**2
        tmp=out/'COMPACT.tmp';tmp.write_bytes(raw);tmp.replace(out/'COMPACT.json')
    try:
        assert not root.exists(),'immutable runtime already exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,digest in manifest['files'].items():assert sha(BUNDLE/name)==digest,name
        cohort=json.loads((BUNDLE/'COHORT.json').read_text(encoding='utf-8-sig'))
        selected=[r for r in cohort['rows'] if r['asset']==asset];assert len(selected)==5
        assert len({r['marketId'] for r in selected})==5
        assert not ({r['marketId'] for r in selected}&{1830119,1829115,2023609})
        assert sha(REFERENCE)==REF_SHA
        ref=json.loads(REFERENCE.read_text(encoding='utf-8-sig'))
        assert sha(OLD_BUNDLE/'MANIFEST.json')==manifest['frozenHandoffManifestSha256']
        old_manifest=json.loads((OLD_BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==NATIVE_SHA
        for name,digest in ref['baseSourceHashes'].items():
            rel=Path(*name.split('.')).with_suffix('.py')
            if not (OLD/rel).exists():rel=Path(*name.split('.'))/'__init__.py'
            p=OLD/rel;assert sha(p)==digest,name
            dest=root/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        for name,digest in old_manifest['files'].items():
            if not name.endswith('.py') or name.startswith('run_hft244_'):continue
            p=OLD/'tools'/name;assert sha(p)==digest,name;shutil.copy2(p,root/'tools'/name)
        assert sha(BUNDLE/'hft244_pair_active_pool_v1.py')==POOL_SHA
        shutil.copy2(BUNDLE/'hft244_pair_active_pool_v1.py',root/'tools/hft244_pair_active_pool_v1.py')
        (root/'tapes').mkdir(exist_ok=True)
        input_meta=[]
        if asset=='ETH':
            z=zipfile.ZipFile(cohort['ETHsource'])
            assert hashlib.sha256(z.read('cohort.json')).hexdigest()==cohort['ETHcohortSha256']
            for item in selected:
                info=z.getinfo(item['zipMember']);assert info.file_size==item['tapeBytes'] and info.file_size<2000000
                b=z.read(info);dest=root/'tapes'/item['tapeName'];dest.write_bytes(b)
                input_meta.append(dict(item,actualTapeSha256=sha(dest)))
            z.close()
        else:
            for item in selected:
                p=BUNDLE/item['tapeName'];assert sha(p)==item['tapeSha256'];shutil.copy2(p,root/'tapes'/item['tapeName'])
                input_meta.append(dict(item,actualTapeSha256=sha(p)))
        result.update(sourceManifest=manifest,cohortSha256=sha(BUNDLE/'COHORT.json'),sourceMarkets=input_meta,
            baseSourceHashes=ref['baseSourceHashes'],nativeSha256=NATIVE_SHA,referenceSha256=REF_SHA)
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        import hftbacktest._hftbacktest as loaded_native
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        assert Path(loaded_native.__file__).resolve()==binary.resolve() and sha(loaded_native.__file__)==NATIVE_SHA
        result['loadedNativePath']=str(Path(loaded_native.__file__).resolve())
        sys.path.insert(0,str(root));os.chdir(root)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.hft244_pair_confirmed_handoff_v1 import make_sim
        from tools.hft244_pair_active_pool_v1 import make_sim as pool_class,pool_counts
        from tools.hft244_pair_only_anatomy_v1 import receipt_anatomy
        install(minimal.v2.base,binary);Control=make_sim(minimal);Pool=pool_class(minimal)
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in sys.modules)
        for item in input_meta:
            cells={};mid=item['marketId']
            for arm in ('A','C','T','X'):
                sim=None;result['attemptedBE']+=1;save()
                try:
                    cls=Pool if arm=='X' else Control
                    sim=cls(root/'tapes'/item['tapeName'],'T' if arm=='X' else arm)
                    # Literal UP is only the legacy output diagnostic; no winner is loaded by this runner.
                    output=sim.run_minimal('UP')
                    sim._receipt_ledger.reconcile(sim.bt.state_values(0));assert not sim._receipt_invalid
                    receipts=list(sim._receipt_ledger.seen.values());assert len(receipts)<=500
                    for k,o in sim.orders.items():
                        if sim.snap(o)['status'] not in minimal.v2.TERMINAL_STATUSES:assert k in sim.slot_key.values(),k
                    if arm=='X':
                        pool_counts(sim.slot_key,sim._probe_key)
                        assert sim._probe_pool_peak['passive']<=4 and sim._probe_pool_peak['active']<=1
                        assert sim.max_simultaneous_slots<=5
                    else:assert sim.max_simultaneous_slots<=4
                    core=dict(output=output,orders=sim.orders,receipts=receipts,native=sim._receipt_ledger.native)
                    row=dict(asset=asset,marketId=mid,arm=arm,windowStartMs=item['windowStartMs'],windowEndMs=item['windowEndMs'],
                        signature=sig(core),correctness=True,UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,
                        cost=sim.cost,qty=sum(sim.inv.values()),fills=sim.fills,submits=sim.submits,alternations=output['fillSideAlternations'],
                        roleFills=output['roleFills'],maxPhysicalSlots=sim.max_simultaneous_slots,
                        poolPeak=getattr(sim,'_probe_pool_peak',None),extraPassiveAdmissions=getattr(sim,'_probe_pool_extra_admissions',0),
                        mark=sim._probe_mark,ready=sim._probe_ready,stage=sim._probe_stage,events=sim._probe_events,
                        risk=sim._probe_risk,holdClocks=sim._probe_hold_clocks,crossBlocks=sim._probe_cross_blocks,
                        receipts=receipts,native=sim._receipt_ledger.native,
                        sourceCoverage=dict(firstReceivedMs=sim.meta['firstReceivedMs'],lastReceivedMs=sim.meta['lastReceivedMs']))
                    row['receiptAudit']=independent_audit(receipts,row['native'],row['UP'],row['DOWN'],row['cost'])
                    ana=receipt_anatomy(receipts,row)
                    row['anatomy']={k:v for k,v in ana.items() if k!='pairs'}
                    direct=[];order=None
                    if sim._probe_key:
                        order=sim.orders[sim._probe_key]
                        direct=[r for r in receipts if r['order_id']==order['n']]
                    paid=sum(r['qty']*(r['price'] if r['side']==1 else 1-r['price']) for r in direct)
                    assert paid<=1+1e-8
                    if order:assert sum(r['qty'] for r in direct)<=order['qty']+1e-8
                    direct_ids={r['sequence'] for r in direct}
                    row.update(directReceipts=direct,directPayment=paid,takerExercised=any(r['maker']==0 for r in direct),
                        directMatchedAtFill=sum(x['qty'] for x in ana['pairs'] if x['closeSeq'] in direct_ids))
                    if arm!='A':assert row['mark']==cells['A']['mark'],'selection-prefix mismatch'
                    if arm in ('T','X'):assert row['ready']==cells['C']['ready'],'readiness-prefix mismatch'
                    cells[arm]=row;result['rows'].append(row);save()
                except Exception:
                    result['failedAt']=dict(marketId=mid,arm=arm)
                    if sim is not None:result['lastPythonOnly']=dict(stage=sim._probe_stage,mark=sim._probe_mark,events=sim._probe_events)
                    raise
                finally:
                    if sim is not None:sim.close()
            a,c,t,x=(cells[k] for k in ('A','C','T','X'))
            delta=lambda lhs,rhs:{s:lhs[s]-rhs[s] for s in ('UP','DOWN','cost','fills','submits','alternations','qty')}
            dv={s:sum(r['qty'] for r in t['directReceipts'] if (r['side']==1)==(s=='UP'))-t['directPayment'] for s in ('UP','DOWN')}
            result['comparisons'].append(dict(marketId=mid,asset=asset,selected=a['mark'] is not None,
                takerExercised=t['takerExercised'],poolExercised=x['extraPassiveAdmissions']>0,
                C_minus_A=delta(c,a),T_minus_C=delta(t,c),X_minus_T=delta(x,t),T_minus_A=delta(t,a),X_minus_A=delta(x,a),
                directEndpoint=dv,continuationResidual={s:t[s]-c[s]-dv[s] for s in ('UP','DOWN')},
                poolReceiptParity=x['receipts']==t['receipts'],poolNativeParity=x['native']==t['native'],
                activityCollapseT=t['fills']==0 and a['fills']>0,activityCollapseX=x['fills']==0 and a['fills']>0))
            save();print(json.dumps(dict(heartbeat='MARKET_COMPLETE',asset=asset,marketId=mid,completed=len(result['comparisons']),attemptedBE=result['attemptedBE'])),flush=True)
        result['verdict']='CROSSASSET_STRATUM_CORRECTNESS_PASS_NO_PROMOTION'
    except Exception as exc:
        result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),trace=traceback.format_exc(limit=8))
    save();print(json.dumps({k:result.get(k) for k in ('asset','verdict','attemptedBE','elapsedSeconds','error')}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)


if __name__=='__main__':main()
