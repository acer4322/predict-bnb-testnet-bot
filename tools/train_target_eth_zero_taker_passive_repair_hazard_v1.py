from __future__ import annotations
import argparse,bisect,json,math,sqlite3,importlib.util
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,brier_score_loss

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('activebase',HERE/'train_target_eth_repair_taker_escalation_hazard_v1.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
EPS=1e-9; FEATURES=list(base.FEATURES)

def event_clocks(c,mid):
    ev,pp=base.parents(c,mid)
    mu=md=0.0; maker_repair=[]
    for e in ev:
        if str(e['role'])!='MAKER':
            continue
        pre=abs(mu-md); sh=float(e['shares'])
        if str(e['side'])=='UP': mu+=sh
        else: md+=sh
        post=abs(mu-md)
        if post < pre-EPS: maker_repair.append(int(e['event_ms']))
    active_repair=sorted(int(p['firstEventMs']) for p in pp if p['role']=='TAKER' and p.get('effect')=='REPAIR_EFFECT')
    return sorted(maker_repair),active_repair

def split(rows):
    mids=sorted({int(r['market_id']) for r in rows});a=max(1,int(len(mids)*.6));b=max(a+1,int(len(mids)*.8))
    tr=set(mids[:a]);va=set(mids[a:b]);te=set(mids[b:])
    return {k:[r for r in rows if int(r['market_id']) in s] for k,s in [('train',tr),('validation',va),('test',te)]},{'trainMarkets':len(tr),'validationMarkets':len(va),'testMarkets':len(te),'trainMax':max(tr) if tr else None,'validationRange':[min(va),max(va)] if va else None,'testMin':min(te) if te else None}

def mat(rr):
    X=np.asarray([[np.nan if r.get(k) is None else float(r.get(k)) for k in FEATURES] for r in rr],np.float32)
    y=np.asarray([int(r['label_passive_repair_1s']) for r in rr],int)
    return X,y

def metrics(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);ix=np.argsort(-p);k=max(1,int(math.ceil(len(y)*.1))) if len(y) else 0
    return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'brier':float(brier_score_loss(y,p)) if len(y) else None,'top10pctPositiveRate':float(y[ix[:k]].mean()) if k else None,'top10pctRecall':float(y[ix[:k]].sum()/y.sum()) if y.sum()>0 else None,'meanP':float(np.mean(p)) if len(p) else None}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-output',required=True);a=ap.parse_args()
    allrows=base.build(a.db)
    c=sqlite3.connect(f'file:{Path(a.db).resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row
    clocks={}
    try:
        for mid in sorted({int(r['market_id']) for r in allrows}): clocks[mid]=event_clocks(c,mid)
    finally:c.close()
    rows=[]
    for r in allrows:
        if float(r.get('taker_gross') or 0)>EPS: continue
        age=r.get('latest_maker_expansion_age_ms')
        if age is None or not math.isfinite(float(age)): continue
        if float(r.get('maker_abs_net') or 0)<=EPS: continue
        cp=int(r['checkpoint_ms']); mt,tt=clocks[int(r['market_id'])]
        im=bisect.bisect_right(mt,cp);it=bisect.bisect_right(tt,cp)
        nm=mt[im] if im<len(mt) else None;nt=tt[it] if it<len(tt) else None
        lab=int(nm is not None and nm<=cp+1000 and (nt is None or nm<nt))
        z=dict(r);z['label_passive_repair_1s']=lab;rows.append(z)
    parts,sp=split(rows);Xtr,ytr=mat(parts['train']);Xv,yv=mat(parts['validation']);Xt,yt=mat(parts['test'])
    structural=np.all(~np.isfinite(Xtr),axis=0); structural_features=[FEATURES[i] for i,v in enumerate(structural) if v]
    Xtr[:,structural]=0.0;Xv[:,structural]=0.0;Xt[:,structural]=0.0
    model=HistGradientBoostingClassifier(max_iter=300,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=30,l2_regularization=2.0,class_weight='balanced',random_state=9301).fit(Xtr,ytr)
    ptr=model.predict_proba(Xtr)[:,1];pv=model.predict_proba(Xv)[:,1];pt=model.predict_proba(Xt)[:,1]
    art={'version':'TARGET_ETH_ZERO_TAKER_POST_EXCURSION_PASSIVE_REPAIR_HAZARD_V1','features':FEATURES,'structuralZeroFeatures':structural_features,'model':model};joblib.dump(art,a.model_output)
    out={'version':'TARGET_ETH_ZERO_TAKER_POST_EXCURSION_PASSIVE_REPAIR_HAZARD_V1','researchOnly':True,'coverage':{'sourceRows':len(allrows),'domainRows':len(rows),'markets':len({int(r['market_id']) for r in rows})},'split':sp,'features':FEATURES,'structuralZeroFeatures':structural_features,'metrics':{'train':metrics(ytr,ptr),'validation':metrics(yv,pv),'test':metrics(yt,pt)},'modelOutput':a.model_output,'boundary':['Fresh ETH Target only.','Zero-Taker-history post-Maker-expansion states only.','Positive label is next actual Maker fill reducing Maker |net| within 1s and before next REPAIR_EFFECT Taker.','No winner/PnL/future action as feature.','No BTC weights/thresholds transferred.','No runtime authority.']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
