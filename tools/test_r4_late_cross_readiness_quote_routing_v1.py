from __future__ import annotations
import bisect,json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
DB=ROOT/'data/public_research_archive_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/r4_late_cross_readiness_quote_routing_v1.json'
CTX=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_worst_case_pnl','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant']
QUOTE=['weak_bid','weak_ask','weak_mid','weak_spread','weak_bid_cheapness','weak_ask_premium','weak_completion_payoff_bid']

def hgb(seed):
    return HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,min_samples_leaf=25,random_state=seed)

def met(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,float)
    return {'n':int(len(y)),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(y) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None}

def load_public(mids):
    c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');out={}
    for i in range(0,len(mids),80):
        blk=mids[i:i+80]
        if not blk: continue
        q=','.join('?'*len(blk))
        for r in c.execute(f'''select market_id,sampled_at_ms,predict_up_bid,predict_up_ask,predict_up_mid,predict_down_bid,predict_down_ask,predict_down_mid from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms''',blk):
            out.setdefault(int(r['market_id']),[]).append((int(r['sampled_at_ms']),dict(r)))
    c.close();return out

def attach_label(a):
    labs=[]
    for mid,g in a.groupby('market_id',sort=False):
        g=g.sort_values('first_event_ms');ts=g.first_event_ms.astype('int64').to_numpy();fl=g.pre_worst_case_pnl.astype(float).to_numpy();idx=g.index.to_numpy()
        for k,ix in enumerate(idx):
            hi=np.searchsorted(ts,ts[k]+5000,side='right');fut=fl[k+1:hi]
            if len(fut)==0:
                labs.append((ix,np.nan));continue
            pos=np.flatnonzero(fut>=0.0)
            if len(pos)==0:
                labs.append((ix,0));continue
            j=int(pos[0]);labs.append((ix,int(np.all(fut[j:]>=0.0))))
    return a.join(pd.DataFrame(labs,columns=['idx','y']).set_index('idx'))

def main():
    f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan).sort_values(['market_id','first_event_ms']).copy()
    need=CTX+['market_id','first_event_ms','weak_side']
    f=f.dropna(subset=need).copy();f['market_id']=f.market_id.astype(int)
    f=attach_label(f)
    d=f[(f.y.notna())&(f.seconds_left>0)&(f.seconds_left<=60)&(f.pre_worst_case_pnl<0)].copy()
    mids=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];pub=load_public(mids)
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
    ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index]
    n=len(ordered);starts=[]
    if n>=32:
        test=max(6,min(10,n//6))
        for frac in (0.45,0.60,0.75):
            st=int(n*frac)
            if st+test<=n and st>=12: starts.append(st)
        starts=sorted(set(starts))[:3]
    else:test=6
    folds=[]
    for fi,st in enumerate(starts):
        tr_ids=set(ordered[:st]);te_ids=set(ordered[st:st+test]);tr=d[d.market_id.isin(tr_ids)].copy();te=d[d.market_id.isin(te_ids)].copy()
        if tr.y.nunique()<2 or te.y.nunique()<2 or len(te)<20: continue
        mb=hgb(7100+fi).fit(tr[CTX],tr.y);mq=hgb(7200+fi).fit(tr[CTX+QUOTE],tr.y)
        pb=mb.predict_proba(te[CTX])[:,1];pq=mq.predict_proba(te[CTX+QUOTE])[:,1]
        bm=met(te.y,pb);qm=met(te.y,pq)
        folds.append({'fold':fi,'trainMarkets':st,'testMarkets':test,'trainRows':int(len(tr)),'testRows':int(len(te)),'positives':int(te.y.sum()),'BASE':bm,'PLUS_QUOTE_ECON':qm,'increment':{'deltaAuc':qm['auc']-bm['auc'],'deltaAp':qm['ap']-bm['ap'],'logLossImprovement':bm['logLoss']-qm['logLoss']}})
    inc=[x['increment'] for x in folds]
    mean=lambda k: float(np.mean([z[k] for z in inc])) if inc else None
    positives=sum(x['positives'] for x in folds)
    support_ok=len(folds)>=3 and positives>=30
    qualifies=bool(support_ok and all(z['deltaAuc']>=0 for z in inc) and mean('deltaAuc')>=.015 and mean('deltaAp')>0 and mean('logLossImprovement')>0)
    status='TESTED_KEEP_SIGNAL' if qualifies else ('TESTED_INCONCLUSIVE' if not support_ok else 'TESTED_REJECTED')
    art={'version':'R4_LATE_CROSS_READINESS_QUOTE_ROUTING_V1','testId':'R4_LATE_CROSS_READINESS_QUOTE_ROUTING_V1_20260827_0838','status':status,'researchOnly':True,'actionAuthority':False,'layerAssignment':{'weakSidePublicQuoteEconomics':'INFORMATION','portfolioPayoffGeometry':'LOGIC_STATE_CONTEXT','predictStrike':'BELIEF_CONTEXT','placementReadiness':'EXCLUDED_FROM_AUTHORITY','output':'LATE_CROSS_READINESS_INFORMATION_CONTEXT_NOT_ACTION_AUTHORITY'},'question':'In final 0-60s negative-floor states, does strict-past weak-side quote economics add stable information beyond geometry+Predict/strike for durable safe crossing within 5s?','label':'current pre_worst_case_pnl<0; y=1 iff future <=5s first reaches pre_worst_case_pnl>=0 and remains >=0 for all later observed checkpoints through +5s; future Target trajectory offline label only','coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'positiveRate':float(d.y.mean()) if len(d) else None,'medianPublicLagMs':float(d.public_lag_ms.median()) if len(d) else None,'maxPublicLagMs':float(d.public_lag_ms.max()) if len(d) else None},'folds':folds,'summary':{'eligibleFolds':len(folds),'testPositives':positives,'meanDeltaAuc':mean('deltaAuc'),'meanDeltaAp':mean('deltaAp'),'meanLogLossImprovement':mean('logLossImprovement'),'worstFoldDeltaAuc':min([z['deltaAuc'] for z in inc]) if inc else None,'allFoldAucNonnegative':all(z['deltaAuc']>=0 for z in inc) if inc else False,'qualifies':qualifies},'fixedKeepRule':'KEEP_SIGNAL only if >=3 eligible chronological folds, each fold deltaAUC>=0, mean deltaAUC>=0.015, mean deltaAP>0, mean log-loss improvement>0, and >=30 positive-label rows across test folds.','guards':['strict-past public quote <=1500ms','final 0-60s only','current negative floor only','future Target trajectory label only','winner/settlement excluded','no threshold/model/hyperparameter sweep','no Echtgeld fit/ingest','information context only; no action authority','Formation PREPARE authority excluded','2026-08-16 SEALED by source cohort contract']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':art['coverage'],'summary':art['summary'],'folds':folds},ensure_ascii=False))
if __name__=='__main__':main()
