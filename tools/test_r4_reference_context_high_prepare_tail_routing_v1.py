from __future__ import annotations
import bisect,json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/target_strike_distance_formation_mode_rebuild_v2_rows.csv'
SCORES=ROOT/'data/research/r4_v0/hourly/r4_information_belief_state_v0_scores.csv'
DB=ROOT/'data/public_research_archive_v1.db'
PREREG=ROOT/'data/research/r4_v0/hourly/r4_reference_context_high_prepare_tail_routing_v1_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_reference_context_high_prepare_tail_routing_v1.json'
TEST_ID='R4_REFERENCE_CONTEXT_HIGH_PREPARE_TAIL_ROUTING_V1_20260828_0635'
BASE=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_worst_case_pnl','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','belief_placement_readiness_5s']
QUOTE=['weak_bid','weak_ask','weak_mid','weak_spread','weak_bid_cheapness','weak_ask_premium','weak_completion_payoff_bid']
REF=['perp_spot_basis_toward_dominant','spot_minus_chainlink_toward_dominant','chainlink_minus_strike_toward_dominant','abs_spot_minus_chainlink_bps']

def met(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,float)
    return {'n':int(len(y)),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(y) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None}

def load_public(mids):
    c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');out={}
    cols='market_id,sampled_at_ms,predict_up_bid,predict_up_ask,predict_up_mid,predict_down_bid,predict_down_ask,predict_down_mid,perp_spot_basis_bps,spot_minus_chainlink_bps,chainlink_minus_strike_bps'
    for i in range(0,len(mids),80):
        blk=mids[i:i+80];q=','.join('?'*len(blk))
        for r in c.execute(f'''select {cols} from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms''',blk):
            out.setdefault(int(r['market_id']),[]).append((int(r['sampled_at_ms']),dict(r)))
    c.close();return out

def main():
    pre=json.loads(PREREG.read_text(encoding='utf-8'))
    a=pd.read_csv(SRC).sort_values(['market_id','first_event_ms']).copy()
    s=pd.read_csv(SCORES)[['market_id','first_event_ms','belief_placement_readiness_5s']].groupby(['market_id','first_event_ms'],as_index=False).mean()
    a=a.merge(s,on=['market_id','first_event_ms'],how='inner')
    labs=[]
    for mid,g in a.groupby('market_id',sort=False):
        g=g.sort_values('first_event_ms');ts=g.first_event_ms.astype('int64').to_numpy();fl=g.pre_worst_case_pnl.astype(float).to_numpy();idx=g.index.to_numpy()
        for k,ix in enumerate(idx):
            hi=np.searchsorted(ts,ts[k]+15000,side='right');fut=fl[k+1:hi]
            labs.append((ix,np.nan if len(fut)==0 else int(np.nanmin(fut)<fl[k]-1e-12)))
    a=a.join(pd.DataFrame(labs,columns=['idx','y']).set_index('idx'))
    d=a[(a['mode'].astype(str)=='REPAIR') & a.y.notna() & (a.seconds_left>60) & (a.seconds_left<=300)].copy()
    mids=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];pub=load_public(mids)
    vals=[]
    for r in d.itertuples(index=False):
        arr=pub.get(int(r.market_id),[])
        if not arr: vals.append(None); continue
        times=[x[0] for x in arr];j=bisect.bisect_left(times,int(r.first_event_ms))-1
        if j<0: vals.append(None); continue
        t,z=arr[j];lag=int(r.first_event_ms)-t
        if lag<0 or lag>1500: vals.append(None); continue
        side=str(r.weak_side); b=z['predict_up_bid'] if side=='UP' else z['predict_down_bid']; ask=z['predict_up_ask'] if side=='UP' else z['predict_down_ask']; mid=z['predict_up_mid'] if side=='UP' else z['predict_down_mid']
        refs=[z['perp_spot_basis_bps'],z['spot_minus_chainlink_bps'],z['chainlink_minus_strike_bps']]
        if any(x is None for x in (b,ask,mid)) or any(x is None for x in refs): vals.append(None); continue
        dom=str(r.dominant_side); sign=1.0 if dom=='UP' else -1.0
        vals.append((lag,float(b),float(ask),float(mid),float(refs[0])*sign,float(refs[1])*sign,float(refs[2])*sign,abs(float(refs[1]))))
    d['_p']=vals;d=d[d._p.notna()].copy()
    names=['public_lag_ms','weak_bid','weak_ask','weak_mid']+REF
    for k,nm in enumerate(names): d[nm]=d._p.map(lambda x:x[k])
    d.drop(columns=['_p'],inplace=True)
    d['weak_spread']=d.weak_ask-d.weak_bid;d['weak_bid_cheapness']=d.weak_mid-d.weak_bid;d['weak_ask_premium']=d.weak_ask-d.weak_mid;d['weak_completion_payoff_bid']=1-d.weak_bid;d['y']=d.y.astype(int)
    ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index]; n=len(ordered); starts=[24,32,40]; test=8; starts=[x for x in starts if x+test<=n]
    folds=[]
    for fi,st in enumerate(starts):
        tr=d[d.market_id.isin(ordered[:st])].copy();te=d[d.market_id.isin(ordered[st:st+test])].copy()
        if tr.y.nunique()<2: continue
        q1,q2=np.quantile(tr.belief_placement_readiness_5s,[1/3,2/3])
        feats0=BASE+QUOTE; feats1=BASE+QUOTE+REF
        mb=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=640+fi).fit(tr[feats0],tr.y)
        mr=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=640+fi).fit(tr[feats1],tr.y)
        te=te.copy();te['_pb']=mb.predict_proba(te[feats0])[:,1];te['_pr']=mr.predict_proba(te[feats1])[:,1]
        mask=te.belief_placement_readiness_5s>q2; z=te[mask]
        if len(z)>=30 and z.y.nunique()>1:
            bm=met(z.y,z._pb);rm=met(z.y,z._pr);inc={'deltaAuc':rm['auc']-bm['auc'],'deltaAp':rm['ap']-bm['ap'],'logLossImprovement':bm['logLoss']-rm['logLoss']}
            high={'eligible':True,'BASE_PLUS_QUOTE':bm,'PLUS_REFERENCE_CONTEXT':rm,'increment':inc}
        else: high={'eligible':False,'n':int(len(z)),'rate':float(z.y.mean()) if len(z) else None}
        folds.append({'fold':fi,'trainMarkets':st,'testMarkets':test,'trainRows':int(len(tr)),'testRows':int(len(te)),'trainReadinessTertiles':[float(q1),float(q2)],'highPrepare':high})
    hi=[f['highPrepare']['increment'] for f in folds if f['highPrepare'].get('eligible')]
    mean=lambda k: float(np.mean([x[k] for x in hi])) if hi else None
    keep=bool(len(hi)>=3 and all(x['deltaAuc']>=0 for x in hi) and mean('deltaAuc')>=.01 and mean('deltaAp')>0 and mean('logLossImprovement')>0)
    status='TESTED_KEEP_SIGNAL' if keep else ('TESTED_INCONCLUSIVE' if len(hi)<3 else 'TESTED_REJECTED')
    art={'version':'R4_REFERENCE_CONTEXT_HIGH_PREPARE_TAIL_ROUTING_V1','testId':TEST_ID,'status':status,'researchOnly':True,'actionAuthority':False,'preregistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'layerAssignment':pre['layerAssignment'],'semanticDifferenceFromPrior':pre['semanticDifferenceFromPrior'],'coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'damageRate':float(d.y.mean()),'medianPublicLagMs':float(d.public_lag_ms.median()),'maxPublicLagMs':float(d.public_lag_ms.max())},'features':{'BASE_PLUS_RETAINED_QUOTE':BASE+QUOTE,'REFERENCE_CONTEXT':REF},'folds':folds,'primaryHighPrepareSummary':{'eligibleFolds':len(hi),'meanDeltaAuc':mean('deltaAuc'),'meanDeltaAp':mean('deltaAp'),'meanLogLossImprovement':mean('logLossImprovement'),'worstDeltaAuc':float(min([x['deltaAuc'] for x in hi])) if hi else None,'allFoldAucNonnegative':all(x['deltaAuc']>=0 for x in hi) if hi else False},'fixedKeepRule':pre['fixedKeepRule'],'decision':status,'authority':'FORMATION_TAIL_INFORMATION_ROUTING_ONLY_NOT_ACTION_AUTHORITY','guards':pre['guards']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':art['coverage'],'primary':art['primaryHighPrepareSummary'],'folds':[f['highPrepare'] for f in folds]},ensure_ascii=False))
if __name__=='__main__': main()
