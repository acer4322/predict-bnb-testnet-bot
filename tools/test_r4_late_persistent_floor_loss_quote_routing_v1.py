from __future__ import annotations
import bisect,json,sqlite3,importlib.util,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_late_crossing_quote_tail_routing_v1.py'
spec=importlib.util.spec_from_file_location('late_quote_base',P);b=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=b;spec.loader.exec_module(b)
FORMATION=b.FORMATION;DB=b.DB;CTX=b.CTX;QUOTE=b.QUOTE
OUT=ROOT/'data/research/r4_v0/hourly/r4_late_persistent_floor_loss_quote_routing_v1.json'

def attach_label(a):
    labs=[]
    for mid,g in a.groupby('market_id',sort=False):
        g=g.sort_values('first_event_ms');ts=g.first_event_ms.astype('int64').to_numpy();fl=g.pre_worst_case_pnl.astype(float).to_numpy();idx=g.index.to_numpy()
        for k,ix in enumerate(idx):
            hi=np.searchsorted(ts,ts[k]+10000,side='right'); y=0; observable=False
            for j in range(k+1,hi):
                if fl[j] <= 0:
                    end=np.searchsorted(ts,min(ts[j]+3000,ts[k]+10000),side='right')
                    seg=fl[j:end]; segts=ts[j:end]
                    if len(segts) and segts[-1]-ts[j] >= 2000:
                        observable=True; y=int(np.nanmax(seg)<=0.0)
                    break
            labs.append((ix,y if observable else np.nan))
    return a.join(pd.DataFrame(labs,columns=['idx','y']).set_index('idx'))

def met(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,float)
    return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
    f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan).sort_values(['market_id','first_event_ms']).copy(); need=CTX+['market_id','first_event_ms','weak_side']; f=f.dropna(subset=need);f['market_id']=f.market_id.astype(int);f=attach_label(f)
    d=f[(f.y.notna())&(f.seconds_left>0)&(f.seconds_left<=60)&(f.pre_worst_case_pnl>0)].copy(); mids=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];pub=b.load_public(mids)
    vals=[]
    for z in d.itertuples(index=False):
        arr=pub.get(int(z.market_id),[])
        if not arr: vals.append(None);continue
        times=[x[0] for x in arr];j=bisect.bisect_left(times,int(z.first_event_ms))-1
        if j<0: vals.append(None);continue
        t,s=arr[j];lag=int(z.first_event_ms)-t
        if lag<0 or lag>1500: vals.append(None);continue
        side=str(z.weak_side);bid=s['predict_up_bid'] if side=='UP' else s['predict_down_bid'];ask=s['predict_up_ask'] if side=='UP' else s['predict_down_ask'];mid=s['predict_up_mid'] if side=='UP' else s['predict_down_mid']
        if any(x is None for x in (bid,ask,mid)): vals.append(None);continue
        vals.append((lag,float(bid),float(ask),float(mid)))
    d['_p']=vals;d=d[d._p.notna()].copy();d['public_lag_ms']=d._p.map(lambda x:x[0]);d['weak_bid']=d._p.map(lambda x:x[1]);d['weak_ask']=d._p.map(lambda x:x[2]);d['weak_mid']=d._p.map(lambda x:x[3]);d.drop(columns=['_p'],inplace=True)
    d['weak_spread']=d.weak_ask-d.weak_bid;d['weak_bid_cheapness']=d.weak_mid-d.weak_bid;d['weak_ask_premium']=d.weak_ask-d.weak_mid;d['weak_completion_payoff_bid']=1-d.weak_bid;d['y']=d.y.astype(int)
    ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];n=len(ordered);test=max(6,min(10,n//6));starts=[]
    for frac in (.45,.60,.75):
        st=int(n*frac)
        if st+test<=n and st>=12: starts.append(st)
    starts=sorted(set(starts))[:3];folds=[]
    for fi,st in enumerate(starts):
        tr=d[d.market_id.isin(set(ordered[:st]))];te=d[d.market_id.isin(set(ordered[st:st+test]))]
        if tr.y.nunique()<2 or te.y.nunique()<2 or len(te)<20: continue
        mb=b.hgb(7100+fi).fit(tr[CTX],tr.y);mq=b.hgb(7200+fi).fit(tr[CTX+QUOTE],tr.y);pb=mb.predict_proba(te[CTX])[:,1];pq=mq.predict_proba(te[CTX+QUOTE])[:,1];bm=met(te.y,pb);qm=met(te.y,pq)
        folds.append({'fold':fi,'trainMarkets':st,'testMarkets':test,'trainRows':int(len(tr)),'testRows':int(len(te)),'positives':int(te.y.sum()),'BASE':bm,'PLUS_QUOTE_ECON':qm,'increment':{'deltaAuc':qm['auc']-bm['auc'],'deltaAp':qm['ap']-bm['ap'],'logLossImprovement':bm['logLoss']-qm['logLoss']}})
    inc=[x['increment'] for x in folds];mean=lambda k:float(np.mean([z[k] for z in inc])) if inc else None;positives=sum(x['positives'] for x in folds);support=len(folds)>=3 and positives>=25
    qualifies=bool(support and all(z['deltaAuc']>=0 for z in inc) and mean('deltaAuc')>=.015 and mean('deltaAp')>0 and mean('logLossImprovement')>0);status='TESTED_KEEP_SIGNAL' if qualifies else ('TESTED_INCONCLUSIVE' if not support else 'TESTED_REJECTED')
    art={'version':'R4_LATE_PERSISTENT_FLOOR_LOSS_QUOTE_ROUTING_V1','testId':'R4_LATE_PERSISTENT_FLOOR_LOSS_QUOTE_ROUTING_V1_20260827_1035','status':status,'researchOnly':True,'actionAuthority':False,'semanticDifference':'Prior late quote test predicted any <=0 floor touch within 10s; this test predicts a break that stays <=0 over an observed 2-3s persistence window.','layerAssignment':{'weakSidePublicQuoteEconomics':'INFORMATION','portfolioPayoffGeometry':'LOGIC_STATE_CONTEXT','predictStrike':'BELIEF_CONTEXT','output':'LATE_CROSSING_PROTECTION_PERSISTENCE_CONTEXT_NOT_ACTION_AUTHORITY'},'label':'current floor>0; y=1 only if floor breaks <=0 within 10s and remains <=0 for observed checkpoints spanning at least 2s, up to 3s after first break','coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'positiveRate':float(d.y.mean()) if len(d) else None,'medianPublicLagMs':float(d.public_lag_ms.median()) if len(d) else None,'maxPublicLagMs':float(d.public_lag_ms.max()) if len(d) else None},'folds':folds,'summary':{'eligibleFolds':len(folds),'testPositives':positives,'meanDeltaAuc':mean('deltaAuc'),'meanDeltaAp':mean('deltaAp'),'meanLogLossImprovement':mean('logLossImprovement'),'worstFoldDeltaAuc':min([z['deltaAuc'] for z in inc]) if inc else None,'allFoldAucNonnegative':all(z['deltaAuc']>=0 for z in inc) if inc else False,'qualifies':qualifies},'fixedKeepRule':'KEEP only if >=3 folds, >=25 persistent-loss positives, every deltaAUC>=0, mean deltaAUC>=0.015, mean deltaAP>0, mean log-loss improvement>0.','guards':['strict-past quote <=1500ms','final 0-60s current positive floor','future Target trajectory offline label only','2026-08-16 SEALED','no Echtgeld fit/ingest','no threshold/model/hyperparameter sweep','no action authority']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':art['coverage'],'summary':art['summary']},ensure_ascii=False))
if __name__=='__main__':main()
