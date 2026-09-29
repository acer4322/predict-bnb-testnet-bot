from __future__ import annotations
import bisect, glob, json, lzma, math, os, sqlite3
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, balanced_accuracy_score

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_profit_regime_selector_a2b_v1.json'
FEATURES=[
 'absNet','floor','upside','totalReserved','sameReserved','oppReserved','rem','marginal','absNetAll','cumExecQty','quoteOffsetTicks','orderAgeS',
 'carrierIsWeak','pMakerUp','pMakerDown','pMakerSum','pMakerGap','pResidualWake','pTaker3s','makerCoverage','makerAvgPairEdge','combinedCoverage','sourceAbsNet','sourceFloor','sourceUpside','activeMakerOrders','directionStrength','directionAlignDominant'
]

def load_pair_results():
    b={}; h={}
    for pat in ['data/research/lan_worker_returns/r4-cont-h2s-chal24-*','data/research/lan_worker_returns/r4-cont-h2s-chalb-*']:
        for p in glob.glob(str(ROOT/pat)):
            d=json.loads(Path(p,'result.json').read_text(encoding='utf-8'))
            for r in d.get('rows') or []:
                if r.get('config')=='FLEX_W5000': b[int(r['marketId'])]=r
                elif r.get('config')=='CONT_STATE_H2_SUSPEND': h[int(r['marketId'])]=r
    return b,h

def load_diag():
    out={}
    for p in glob.glob(str(ROOT/'data/research/lan_worker_returns/r4-cont-h2diag-v20-*')):
        d=json.loads(Path(p,'result.json').read_text(encoding='utf-8'))
        for r in d.get('rows') or []: out[int(r['marketId'])]=r
    return out

def cohort_sets():
    a=json.loads((ROOT/'data/research/r4_v0/p0_provenance_v1/r4_cont_h2_suspend_challenge24_wave_20260831.json').read_text(encoding='utf-8'))
    b=json.loads((ROOT/'data/research/r4_v0/p0_provenance_v1/r4_cont_h2_suspend_challenge24_b_wave_20260831.json').read_text(encoding='utf-8'))
    def mids(d):
        z=[]
        for j in d['jobs']:
            av=j['argv']; z.extend(int(x) for x in av[av.index('--ids')+1].split(','))
        return set(z)
    return mids(a),mids(b)

def source_file(mid):
    aroot=ROOT/'data/research/lan_worker_bundles/r4_cont_h2_suspend_challenge24_20260831/data/hft_forward_paper_v1/markets'
    broot=ROOT/'data/research/lan_worker_bundles/r4_cont_h2_suspend_challenge24_b_20260831/data/hft_forward_paper_v1/markets'
    for root in (aroot,broot):
        p=root/f'{mid}_r2_hft_closed_loop_v1.json.xz'
        if p.exists(): return p
    return None

def latest_decision(mid,t):
    p=source_file(mid)
    if p is None:return {}
    with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
    rr=d.get('decisionRows') or []; ts=[int(x.get('decisionMs') or 0) for x in rr]; j=bisect.bisect_right(ts,int(t))-1
    return rr[j] if j>=0 else {}

def pnl(r,w):
    f=r['final'];return (float(f['up']) if w=='UP' else float(f['down']))-float(f['cost'])

def fv(x,default=math.nan):
    try:
        z=float(x); return z if math.isfinite(z) else default
    except Exception:return default

def row_for(mid,diag,base,h2,winner):
    tr=diag.get('continuousActionTrace') or []
    first=next((x for x in tr if x.get('event')=='PULL_NEGATIVE'),None)
    if first is None:return None
    z=latest_decision(mid,int(first['t'])); m=z.get('models') or {}; p=z.get('portfolio') or {}; dr=z.get('direction') or {}
    side=str(first.get('side')); weak=str(first.get('weakSide')); dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
    dside=str(dr.get('side') or 'NEUTRAL')
    upres=fv(first.get('upReserved'),0.0); dnres=fv(first.get('downReserved'),0.0)
    same=upres if side=='UP' else dnres; opp=dnres if side=='UP' else upres
    absn=fv(first.get('absNet'),0.0); fl=fv(first.get('floor'),0.0)
    pu=fv(m.get('pMakerUp'),0.0); pd=fv(m.get('pMakerDown'),0.0)
    vals={
      'absNet':absn,'floor':fl,'upside':fl+absn,'totalReserved':upres+dnres,'sameReserved':same,'oppReserved':opp,
      'rem':fv(first.get('rem'),0.0),'marginal':fv(first.get('marginal'),0.0),'absNetAll':abs(fv(first.get('netAll'),0.0)),
      'cumExecQty':fv(first.get('cumExecQty'),0.0),'quoteOffsetTicks':fv(first.get('quoteOffsetTicks')),'orderAgeS':fv(first.get('orderAgeMs'),0.0)/1000.0,
      'carrierIsWeak':1.0 if side==weak else 0.0,'pMakerUp':pu,'pMakerDown':pd,'pMakerSum':pu+pd,'pMakerGap':abs(pu-pd),
      'pResidualWake':fv(m.get('pResidualWake'),0.0),'pTaker3s':fv(m.get('pTaker3s'),0.0),
      'makerCoverage':fv(p.get('maker_paired_coverage')),'makerAvgPairEdge':fv(p.get('maker_avg_pair_edge')),'combinedCoverage':fv(p.get('combined_paired_coverage')),
      'sourceAbsNet':fv(p.get('combined_abs_net')),'sourceFloor':fv(p.get('worst_case_floor')),'sourceUpside':fv(p.get('best_case_pnl')),
      'activeMakerOrders':fv(z.get('activeMakerOrders'),0.0),'directionStrength':fv(dr.get('strength'),0.0),'directionAlignDominant':1.0 if dom is not None and dside==dom else 0.0,
    }
    bp=pnl(base,winner); hp=pnl(h2,winner)
    return {'marketId':mid,'t':int(first['t']),'features':vals,'flexPnl':bp,'h2Pnl':hp,'h2Delta':hp-bp,'labelH2Better':int(hp>bp+1e-9),'winner':winner}

def main():
    base,h2=load_pair_results(); diag=load_diag(); aset,bset=cohort_sets(); mids=sorted(set(base)&set(h2)&set(diag))
    db=sqlite3.connect(str(ROOT/'data/target_wallet_official_v1.db')); q=','.join('?'*len(mids)); wins={int(a):str(b) for a,b in db.execute(f'select market_id,winner from target_markets where market_id in ({q})',mids) if b in ('UP','DOWN')};db.close()
    rows=[]
    for mid in mids:
        if mid not in wins:continue
        r=row_for(mid,diag[mid],base[mid],h2[mid],wins[mid])
        if r: rows.append(r)
    train=[r for r in rows if r['marketId'] in aset]; test=[r for r in rows if r['marketId'] in bset]
    Xtr=np.array([[r['features'].get(f,math.nan) for f in FEATURES] for r in train],float); ytr=np.array([r['labelH2Better'] for r in train],int)
    Xte=np.array([[r['features'].get(f,math.nan) for f in FEATURES] for r in test],float); yte=np.array([r['labelH2Better'] for r in test],int)
    model=Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('scale',StandardScaler()),('lr',LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260831))])
    model.fit(Xtr,ytr); ptr=model.predict_proba(Xtr)[:,1]; pte=model.predict_proba(Xte)[:,1]; pred=(pte>=.5).astype(int)
    selected=[]
    for r,pr,pp in zip(test,pred,pte):
        chosen='H2' if pr else 'FLEX'; cp=r['h2Pnl'] if pr else r['flexPnl']; selected.append({'marketId':r['marketId'],'probH2':float(pp),'pred':chosen,'actualBetter':'H2' if r['labelH2Better'] else 'FLEX','chosenPnl':cp,'flexPnl':r['flexPnl'],'h2Pnl':r['h2Pnl']})
    # stitched result is diagnostic only; a causal replay selector must be implemented before promotion.
    test_flex=sum(r['flexPnl'] for r in test); test_h2=sum(r['h2Pnl'] for r in test); test_sel=sum(r['chosenPnl'] for r in selected); test_oracle=sum(max(r['flexPnl'],r['h2Pnl']) for r in test)
    out={'version':'R4_PROFIT_REGIME_SELECTOR_A2B_V1','researchOnly':True,'actionAuthority':False,'causalReplayRequired':True,
         'trainCohort':'Challenge A','testCohort':'Challenge B','features':FEATURES,'model':'median+indicator -> StandardScaler -> LogisticRegression(C=0.5,class_weight=balanced,threshold=0.5,seed=20260831)',
         'trainN':len(train),'testN':len(test),'trainPositive':int(ytr.sum()),'testPositive':int(yte.sum()),
         'trainAuc':float(roc_auc_score(ytr,ptr)) if len(set(ytr))>1 else None,'testAuc':float(roc_auc_score(yte,pte)) if len(set(yte))>1 else None,
         'testBalancedAccuracy':float(balanced_accuracy_score(yte,pred)) if len(set(yte))>1 else None,
         'testPnl':{'FLEX':test_flex,'H2':test_h2,'stitchedSelectorDiagnostic':test_sel,'oracle':test_oracle},
         'testWinnerCountByChosenPnl':sum(x['chosenPnl']>0 for x in selected),'selectedH2':int(pred.sum()),'selectedFLEX':int((1-pred).sum()),
         'selectedRows':selected,'rows':rows,
         'boundary':'Outcome labels/PnL are offline teacher/scoring only. The stitched selector PnL is NOT causal policy evidence because H2 and FLEX trajectories differ before/at the first H2 pull seam; implement the frozen selector inside replay before any promotion claim.'}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({k:v for k,v in out.items() if k not in {'rows','selectedRows'}},ensure_ascii=False,indent=2));print('SELECTED',json.dumps(selected,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
