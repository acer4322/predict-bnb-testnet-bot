from __future__ import annotations
import json,lzma,math,sqlite3
from pathlib import Path
import joblib, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score,average_precision_score,brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'; DB=ROOT/'data/hft_forward_paper_v1.db'; DIR=ROOT/'data/hft_forward_paper_v1/markets'
LAND=[15000,30000,45000,60000,90000,120000]
FEATURES=['orderAgeMs','price','remainingQty','partialFillRatio','activeSameCount','activeOppCount','quoteOffsetTicks','currentBid','currentAsk','currentSpreadTicks','initialDepth','publicCumDepletion','maker_abs_net','combined_abs_net','combined_paired_coverage','worst_case_floor']

def f(v,d=0.):
    try:x=float(v)
    except:return d
    return x if math.isfinite(x) else d

def rows_for_market(m):
    p=DIR/f'{m}_r2_hft_closed_loop_v1.json.xz'
    if not p.exists():return []
    r=json.load(lzma.open(p,'rt',encoding='utf-8')); states=r.get('orderStateRows') or []
    by={}
    for s in states:
        age=f(s.get('orderAgeMs'),-1); status=str(s.get('hftStatus') or '')
        if age<10000 or status in {'FILLED','CANCELED','EXPIRED'}:continue
        oid=str(s.get('orderId') or '');
        if not oid:continue
        for lm in LAND:
            dist=abs(age-lm)
            if dist<=3000:
                key=(oid,lm); old=by.get(key)
                if old is None or dist<old[0]:by[key]=(dist,s)
    out=[]
    for (oid,lm),(_,s) in by.items():
        p=s.get('portfolio') or {}; delay=s.get('futureFirstFillDelayMs'); y=int(delay is not None and f(delay,1e12)>0 and f(delay,1e12)<=30000)
        eventual=int(f(s.get('eventualAdditionalFillShares'))>0)
        x=[f(s.get('orderAgeMs')),f(s.get('price')),f(s.get('remainingQty')),f(s.get('partialFillRatio')),f(s.get('activeSameCount')),f(s.get('activeOppCount')),f(s.get('quoteOffsetTicks')),f(s.get('currentBid')),f(s.get('currentAsk')),f(s.get('currentSpreadTicks')),f(s.get('initialDepth')),f(s.get('publicCumDepletion')),f(p.get('maker_abs_net')),f(p.get('combined_abs_net')),f(p.get('combined_paired_coverage')),f(p.get('worst_case_floor'))]
        out.append({'marketId':m,'orderId':oid,'landmarkMs':lm,'x':x,'y30':y,'eventual':eventual})
    return out

def metrics(model,rows):
    if not rows:return {}
    X=np.asarray([r['x'] for r in rows]); y=np.asarray([r['y30'] for r in rows]); pr=model.predict_proba(X)[:,1]
    return {'n':len(rows),'positiveRate':float(y.mean()),'auc':None if len(set(y))<2 else float(roc_auc_score(y,pr)),'ap':float(average_precision_score(y,pr)),'brier':float(brier_score_loss(y,pr)),'meanPred':float(pr.mean())}

def main():
    con=sqlite3.connect(DB); ids=[int(r[0]) for r in con.execute("select distinct market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by market_id")]; con.close()
    allr=[]
    for m in ids:allr+=rows_for_market(m)
    mids=sorted({r['marketId'] for r in allr}); cut=max(1,int(len(mids)*.8)); trset=set(mids[:cut]); vaset=set(mids[cut:]); tr=[r for r in allr if r['marketId'] in trset]; va=[r for r in allr if r['marketId'] in vaset]
    model=Pipeline([('scale',StandardScaler()),('lr',LogisticRegression(C=0.5,class_weight='balanced',max_iter=1000,random_state=20260825))]); model.fit(np.asarray([r['x'] for r in tr]),np.asarray([r['y30'] for r in tr]))
    val_by_age=[]
    for lm in LAND:
        g=[r for r in va if r['landmarkMs']==lm]; met=metrics(model,g); met['ageSec']=lm/1000; met['eventualLateFillRate']=sum(r['eventual'] for r in g)/len(g) if g else None; val_by_age.append(met)
    artifact={'version':'HFT_R21_LONG_RESTING_PATIENCE_MODEL_V2','features':FEATURES,'landmarksMs':LAND,'model':model,'trainMarkets':sorted(trset),'validationMarkets':sorted(vaset)}
    joblib.dump(artifact,OUT/'hft_r21_long_resting_patience_model_v2.joblib')
    rep={'version':artifact['version'],'researchOnly':True,'actionAuthority':False,'target':'P(fill within next 30s | still live at landmark)','features':FEATURES,'train':metrics(model,tr),'validation':metrics(model,va),'validationByAge':val_by_age,'marketSplit':{'train':len(trset),'validation':len(vaset)},'decision':'KEEP_PATIENCE_STATE_MODEL_FOR_MATCHED_ACTION_CURRICULUM' if metrics(model,va).get('auc',0)>=0.6 else 'REJECT_PATIENCE_STATE_MODEL_V2','note':'No action threshold is promoted. Model is information for R2 re-evaluation curriculum only.'}
    (OUT/'hft_r21_long_resting_patience_model_v2_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
