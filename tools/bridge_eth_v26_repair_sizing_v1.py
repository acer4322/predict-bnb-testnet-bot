from __future__ import annotations
import argparse,bisect,importlib.util,json,math,statistics
from pathlib import Path
import numpy as np,joblib

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('br',HERE/'bridge_eth_v23_repair_taker_hazard_v1.py');br=importlib.util.module_from_spec(spec);spec.loader.exec_module(br)
FEATURES=list(br.FEATURES); EPS=1e-9

def qtile(xs,q):
    ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not ys:return None
    z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
    return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9),'min':min(ys) if ys else None,'max':max(ys) if ys else None}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--shadow',default='data/research/r4_v0/p0_provenance_v1/ETH_V26_ACTIVE_REPAIR_SHADOW_ECONOMICS_RESULT.json');ap.add_argument('--book-db',default='data/wallet_maker_book_inference_eth5m.db');ap.add_argument('--model',required=True);ap.add_argument('--raw',nargs='*',default=br.RAW_DEFAULT);ap.add_argument('--output',required=True);a=ap.parse_args()
    sh=json.load(open(a.shadow,encoding='utf-8')); art=joblib.load(a.model); model=art['model']; assert list(art['features'])==FEATURES
    raw,_=br.load_raw(a.raw)
    import sqlite3;c=sqlite3.connect(f'file:{Path(a.book_db).resolve().as_posix()}?mode=ro',uri=True)
    rows=[]
    try:
        for r in sh['rows']:
            mid=int(r['marketId']); t=int(r['warningAt']); rr=raw.get(mid)
            if rr is None:continue
            events=br.annotate_fills(rr['V23']); inv=br.make_inventory(events,t); ph=br.parent_history(events,t)
            meta=c.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone()
            if meta is None:continue
            mend=int(meta[0]); states=br.bookbase.load_states(c,mid,t-3000,t+1,'received_at_ms'); ts=[s[0] for s in states]; idx=bisect.bisect_right(ts,t)-1
            if idx<0:continue
            st=states[idx]; age=t-int(st[0])
            if age<0 or age>2000:continue
            z=br.feature_row(inv,ph,t,mend,st)
            if z is None:continue
            x,vals=z; pred=float(np.clip(model.predict(x.reshape(1,-1))[0],0,1)); rem=float(r['remainingResponsibility']); depth=float(r['repairAskDepth']); rawq=pred*rem; q=max(0.0,min(rawq,rem,depth))
            rows.append({'marketId':mid,'cycleIndex':int(r['cycleIndex']),'completedWithin30s':bool(r['completedWithin30s']),'warningAt':t,'bookAgeMs':age,
                         'predictedRepairFraction':pred,'remainingResponsibility':rem,'rawPredictedQty':rawq,'bestAskDepth':depth,'boundedShadowQty':q,
                         'boundedFractionOfResidual':q/rem if rem>EPS else 0.0,'ownedBoundRespected':q<=rem+EPS,'depthBoundRespected':q<=depth+EPS,
                         'repairAsk':float(r['repairAsk']),'pairSumAtAsk':float(r['pairSumAtAsk']),'shadowPairGainPerShare':float(r['shadowPairGainPerShare']),
                         'floorBefore':float(r['floorBefore']),'fullResidualShadowFloorAfter':float(r['shadowFloorAfter']),'teacherMaxHazard':float(r['teacherMaxHazard'])})
    finally:c.close()
    comp=[r for r in rows if r['completedWithin30s']];fail=[r for r in rows if not r['completedWithin30s']]
    preds=[r['predictedRepairFraction'] for r in rows]
    out={'version':'ETH_V26_REPAIR_SIZING_BRIDGE_V1','researchOnly':True,'coverage':{'shadowRows':len(sh['rows']),'scoredRows':len(rows),'completed':len(comp),'failed':len(fail)},
         'summary':{'predictedRepairFraction':stats(preds),'completedPredictedFraction':stats([r['predictedRepairFraction'] for r in comp]),'failedPredictedFraction':stats([r['predictedRepairFraction'] for r in fail]),
                    'boundedFractionOfResidual':stats([r['boundedFractionOfResidual'] for r in rows]),'boundaryHitZeroRate':sum(p<=1e-9 for p in preds)/len(preds) if preds else None,'boundaryHitOneRate':sum(p>=1-1e-9 for p in preds)/len(preds) if preds else None,
                    'allOwnedBoundsRespected':all(r['ownedBoundRespected'] for r in rows),'allDepthBoundsRespected':all(r['depthBoundRespected'] for r in rows)},
         'rows':rows,'boundary':['Frozen ETH sizing teacher; no refit.','Strict-past OUR state at frozen V26 warning.','Quantity bounded by remaining deterministic Repair responsibility and best-ask depth.','No ADD creation and no Taker submission.']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
