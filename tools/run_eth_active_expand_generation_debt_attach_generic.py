from __future__ import annotations
import argparse,json,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_active_expand_generation_debt_attach_1946653 as impl
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
EPS=1e-9; pe=base.pe

def load_baseline(path,mid):
    bj=json.load(open(path,encoding='utf-8'))
    rows=bj.get('rows') if isinstance(bj,dict) else None
    if rows:
        for x in rows:
            if int(x.get('marketId'))==mid:return x
    if isinstance(bj,dict) and int(bj.get('marketId',mid))==mid:return bj
    raise KeyError(mid)

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True)
    ap.add_argument('--baseline-json',required=True)
    ap.add_argument('--output',required=True)
    a=ap.parse_args();mid=int(a.market_id);tmp=Path(tempfile.mkdtemp(prefix=f'active_expand_gen_debt_generic_{mid}_'));stop=threading.Event();started=time.time()
    def hb():
        while not stop.wait(15):
            print(json.dumps({'heartbeat':'ACTIVE_EXPAND_GENERATION_DEBT_GENERIC','marketId':mid,'elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'ACTIVE_EXPAND_GENERATION_DEBT_GENERIC_START','marketId':mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        b=load_baseline(a.baseline_json,mid)
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        tape=tmp/'tapes'/f'{mid}.json.xz'
        s=pe.make(impl.ActiveExpandGenerationDebtAttachHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:
            r=s.run_candidate(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {};pay=s._current_payoffs()
        finally:s.close()
        m=base.slim(r);ss=pe.safety(r);occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True
        bp=(b.get('allocationParents') or {}).get('1') or next(iter((b.get('allocationParents') or {}).values()),{})
        cp=parents.get('1') or parents.get(1) or {}
        base_pnl=float(b.get('pnlDiagnosticOnly') or 0);base_floor=float(b.get('floor') or 0);base_fills=int(b.get('fills') or 0);base_rounds=int(b.get('rounds') or 0);base_active=float(b.get('parallelRepairActiveFillQty') or 0)
        parity=(m['fills']==base_fills and m['rounds']==base_rounds and abs(m['pnlDiagnosticOnly']-base_pnl)<=1e-9 and abs(m['floor']-base_floor)<=1e-9 and abs(m['activeRepairFillQty']-base_active)<=1e-9)
        events=r.get('activeExpandGenerationDebtEvents') or []
        attached=sum(float(x.get('added_debt') or 0) for x in events if x.get('applied'))
        expected_initial=float(bp.get('initialDebt') or 0)+attached
        gates={
            'attachExercised':int(r.get('activeExpandGenerationDebtAttached') or 0)>0,
            'behaviorParityVsBaseline':parity,
            'allocationConservation':bool(cons),
            'allocationParentDebtBounded':bool(bound),
            'executionOccupancyBounded':bool(occok),
            'safetyZero':all(float(v or 0)<=EPS for v in ss.values()),
            'initialDebtMatchesBaselinePlusExpandFill':abs(float(cp.get('initialDebt') or 0)-expected_initial)<=1e-7,
            'overflowNonIncreasing':float(cp.get('overflowBorn') or 0)<=float(bp.get('overflowBorn') or 0)+1e-7,
            'remainingDebtMatchesTerminalGap':abs(float(cp.get('remainingDebt') or 0)-abs(float(pay.get('gap') or 0)))<=1e-7,
        }
        if not gates['attachExercised']:decision='GENERIC_ATTACH_NOT_EXERCISED'
        elif not all(v for k,v in gates.items() if k!='behaviorParityVsBaseline'):decision='GENERIC_ATTACH_SAFETY_OR_ALIGNMENT_FAIL'
        elif parity:decision='GENERIC_ATTACH_BEHAVIOR_INERT_PASS'
        else:decision='GENERIC_ATTACH_BEHAVIOR_CHANGED_DIAGNOSE'
        out={
            'version':'ETH_ACTIVE_EXPAND_GENERATION_DEBT_ATTACH_GENERIC_V1','date':'2026-09-05','researchOnly':True,'marketId':mid,'decision':decision,
            'baseline':{'pnlDiagnosticOnly':base_pnl,'floor':base_floor,'fills':base_fills,'rounds':base_rounds,'activeRepairFillQty':base_active,'allocationParent':bp},
            'candidate':m,'delta':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-base_pnl,'floor':m['floor']-base_floor,'fills':m['fills']-base_fills,'rounds':m['rounds']-base_rounds},
            'gates':gates,'safety':ss,'terminalPayoffs':pay,'allocationParents':parents,'activeExpandGenerationDebtEvents':events,
            'v84OverflowAllocatedQty':float(r.get('v84OverflowAllocatedQty') or 0),'v84OverflowPaidQty':float(r.get('v84OverflowPaidQty') or 0),
            'boundary':['single variable: confirmed Active Expand fallback fill attaches new generation debt to same parent AllocationLedger V3','no Active ownership/price/qty/timing/multislot behavior change','realistic HFT','no dream fill','no 8781']
        }
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'decision':decision,'candidate':m,'gates':gates,'baselineParent':bp,'candidateParent':cp,'attachEvents':events},ensure_ascii=False),flush=True)
    finally:
        stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
