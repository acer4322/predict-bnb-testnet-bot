from __future__ import annotations
import bisect,json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
FORMATION=ROOT/'data/research/r4_v0/hourly/target_strike_distance_formation_mode_rebuild_v2_rows.csv'
SCORES=ROOT/'data/research/r4_v0/hourly/r4_information_belief_state_v0_scores.csv'
PUBDB=ROOT/'data/public_research_archive_v1.db'
PREREG=ROOT/'data/research/r4_v0/hourly/r4_late_quote_trajectory_protection_routing_v1_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_late_quote_trajectory_protection_routing_v1.json'
TEST_ID='R4_LATE_QUOTE_TRAJECTORY_PROTECTION_ROUTING_V1_20260828_1836'
CTX=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_worst_case_pnl','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant']
QUOTE=['weak_bid','weak_ask','weak_mid','weak_spread','weak_bid_cheapness','weak_ask_premium','weak_completion_payoff_bid']
READINESS=['belief_placement_readiness_5s']
TRAJ=['weak_bid_delta_10s','weak_ask_delta_10s','weak_mid_delta_10s','weak_spread_delta_10s']

def hgb(seed):
    return HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,min_samples_leaf=25,random_state=seed)

def met(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,float)
    return {'n':int(len(y)),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(y) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None}

def load_public(mids):
    c=sqlite3.connect(f'file:{PUBDB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');out={}
    cols='market_id,sampled_at_ms,predict_up_bid,predict_up_ask,predict_up_mid,predict_down_bid,predict_down_ask,predict_down_mid'
    for i in range(0,len(mids),80):
        blk=mids[i:i+80];q=','.join('?'*len(blk))
        for r in c.execute(f'select {cols} from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms',blk):
            out.setdefault(int(r['market_id']),[]).append((int(r['sampled_at_ms']),dict(r)))
    c.close();return out

def attach_label(a):
    labs=[]
    for mid,g in a.groupby('market_id',sort=False):
        g=g.sort_values('first_event_ms');ts=g.first_event_ms.astype('int64').to_numpy();fl=g.pre_worst_case_pnl.astype(float).to_numpy();idx=g.index.to_numpy()
        for k,ix in enumerate(idx):
            hi=np.searchsorted(ts,ts[k]+10000,side='right');fut=fl[k+1:hi]
            labs.append((ix,np.nan if len(fut)==0 else int(np.nanmin(fut)<=0.0)))
    return a.join(pd.DataFrame(labs,columns=['idx','y']).set_index('idx'))

def side_quote(snap,side):
    if side=='UP': return snap['predict_up_bid'],snap['predict_up_ask'],snap['predict_up_mid']
    return snap['predict_down_bid'],snap['predict_down_ask'],snap['predict_down_mid']

def main():
    pre=json.loads(PREREG.read_text(encoding='utf-8'))
    f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan).sort_values(['market_id','first_event_ms']).copy()
    s=pd.read_csv(SCORES)[['market_id','first_event_ms','belief_placement_readiness_5s']].groupby(['market_id','first_event_ms'],as_index=False).mean()
    f=f.merge(s,on=['market_id','first_event_ms'],how='inner')
    need=CTX+READINESS+['market_id','first_event_ms','weak_side']
    f=f.dropna(subset=need).copy();f['market_id']=f.market_id.astype(int);f=attach_label(f)
    d=f[(f.y.notna())&(f.seconds_left>0)&(f.seconds_left<=60)&(f.pre_worst_case_pnl>0)].copy()
    mids=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];pub=load_public(mids)
    vals=[]
    for z in d.itertuples(index=False):
        arr=pub.get(int(z.market_id),[]);now=int(z.first_event_ms);side=str(z.weak_side)
        if not arr: vals.append(None);continue
        times=[x[0] for x in arr];j=bisect.bisect_left(times,now)-1
        old_cut=now-10000;j0=bisect.bisect_right(times,old_cut)-1
        if j<0 or j0<0: vals.append(None);continue
        t1,s1=arr[j];t0,s0=arr[j0];lag=now-t1;old_age=old_cut-t0
        q1=side_quote(s1,side);q0=side_quote(s0,side)
        if lag<0 or lag>1500 or old_age<0 or old_age>1500 or any(x is None for x in q1+q0): vals.append(None);continue
        b1,a1,m1=map(float,q1);b0,a0,m0=map(float,q0)
        vals.append((lag,old_age,b1,a1,m1,b1-b0,a1-a0,m1-m0,(a1-b1)-(a0-b0)))
    d['_v']=vals;d=d[d._v.notna()].copy()
    names=['public_lag_ms','old_anchor_lag_ms','weak_bid','weak_ask','weak_mid']+TRAJ
    for k,nm in enumerate(names): d[nm]=d._v.map(lambda x:x[k])
    d.drop(columns=['_v'],inplace=True)
    d['weak_spread']=d.weak_ask-d.weak_bid;d['weak_bid_cheapness']=d.weak_mid-d.weak_bid;d['weak_ask_premium']=d.weak_ask-d.weak_mid;d['weak_completion_payoff_bid']=1-d.weak_bid;d['y']=d.y.astype(int)
    d=d.dropna(subset=CTX+QUOTE+READINESS+TRAJ+['y'])
    ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];n=len(ordered)
    test=max(3,min(4,n//5));starts=[x for x in (max(8,n-3*test),max(8,n-2*test),max(8,n-test)) if x+test<=n];starts=sorted(set(starts))[:3]
    folds=[];base=CTX+QUOTE+READINESS;aug=base+TRAJ
    for fi,st in enumerate(starts):
        tr=d[d.market_id.isin(set(ordered[:st]))].copy();te=d[d.market_id.isin(set(ordered[st:st+test]))].copy()
        if tr.y.nunique()<2 or te.y.nunique()<2 or len(te)<20: continue
        mb=hgb(9300+fi).fit(tr[base],tr.y);ma=hgb(9400+fi).fit(tr[aug],tr.y)
        pb=mb.predict_proba(te[base])[:,1];pa=ma.predict_proba(te[aug])[:,1];bm=met(te.y,pb);am=met(te.y,pa)
        folds.append({'fold':fi,'trainMarkets':st,'testMarkets':test,'trainRows':int(len(tr)),'testRows':int(len(te)),'positives':int(te.y.sum()),'BASE_LATE_STACK':bm,'PLUS_QUOTE_TRAJECTORY':am,'increment':{'deltaAuc':am['auc']-bm['auc'],'deltaAp':am['ap']-bm['ap'],'logLossImprovement':bm['logLoss']-am['logLoss']}})
    inc=[x['increment'] for x in folds];mean=lambda k: float(np.mean([z[k] for z in inc])) if inc else None;positives=sum(x['positives'] for x in folds)
    support_ok=len(folds)>=3 and positives>=30
    qualifies=bool(support_ok and all(z['deltaAuc']>=0 for z in inc) and mean('deltaAuc')>=.015 and mean('deltaAp')>0 and mean('logLossImprovement')>0)
    status='TESTED_KEEP_SIGNAL' if qualifies else ('TESTED_INCONCLUSIVE' if not support_ok else 'TESTED_REJECTED')
    variation={k:{'std':float(d[k].std()),'min':float(d[k].min()),'max':float(d[k].max()),'nonzeroRate':float((d[k]!=0).mean())} for k in TRAJ}
    art={'version':'R4_LATE_QUOTE_TRAJECTORY_PROTECTION_ROUTING_V1','testId':TEST_ID,'status':status,'researchOnly':True,'actionAuthority':False,'preregistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'semanticAxis':pre['semanticAxis'],'semanticDifferenceFromPrior':pre['semanticDifferenceFromPrior'],'layerAssignment':pre['layerAssignment'],'question':pre['hypothesis'],'label':pre['label'],'coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'positiveRate':float(d.y.mean()) if len(d) else None,'medianPublicLagMs':float(d.public_lag_ms.median()) if len(d) else None,'maxPublicLagMs':float(d.public_lag_ms.max()) if len(d) else None,'medianOldAnchorLagMs':float(d.old_anchor_lag_ms.median()) if len(d) else None,'maxOldAnchorLagMs':float(d.old_anchor_lag_ms.max()) if len(d) else None,'trajectoryVariation':variation},'features':{'BASE_LATE_STACK':base,'QUOTE_TRAJECTORY':TRAJ},'folds':folds,'summary':{'eligibleFolds':len(folds),'testPositives':positives,'meanDeltaAuc':mean('deltaAuc'),'meanDeltaAp':mean('deltaAp'),'meanLogLossImprovement':mean('logLossImprovement'),'worstFoldDeltaAuc':min([z['deltaAuc'] for z in inc]) if inc else None,'allFoldAucNonnegative':all(z['deltaAuc']>=0 for z in inc) if inc else False,'qualifies':qualifies},'fixedKeepRule':pre['fixedKeepRule'],'decision':status,'authority':'LATE_PROTECTION_CONTEXT_ONLY_NOT_ACTION_AUTHORITY','guards':pre['guards']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':art['coverage'],'summary':art['summary'],'foldIncrements':[x['increment'] for x in folds]},ensure_ascii=False))
if __name__=='__main__': main()
