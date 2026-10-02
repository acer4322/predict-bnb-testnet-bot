from __future__ import annotations
import bisect,json,sqlite3,math
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/target_strike_distance_formation_mode_rebuild_v2_rows.csv'
SCORES=ROOT/'data/research/r4_v0/hourly/r4_information_belief_state_v0_scores.csv'
DB=ROOT/'data/public_research_archive_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/r4_market_participation_micro_high_prepare_tail_routing_v1.json'
TEST_ID='R4_MARKET_PARTICIPATION_MICRO_HIGH_PREPARE_TAIL_ROUTING_V1_20260828_1038'
BASE=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_worst_case_pnl','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','belief_placement_readiness_5s']
QUOTE=['weak_bid','weak_ask','weak_mid','weak_spread','weak_bid_cheapness','weak_ask_premium','weak_completion_payoff_bid']
MICRO=['spot_return_1s_bps','spot_return_3s_bps','spot_queue_imbalance','spot_taker_imbalance_1s','futures_return_1s_bps','futures_return_3s_bps','futures_queue_imbalance','futures_taker_imbalance_1s']
BASELINE=BASE+QUOTE

def met(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,float)
    return {'n':int(len(y)),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(y) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None}

def finite(x):
    try: return math.isfinite(float(x))
    except: return False

def load_public(mids):
    cols=['market_id','sampled_at_ms','predict_up_bid','predict_up_ask','predict_up_mid','predict_down_bid','predict_down_ask','predict_down_mid']+MICRO
    c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');out={}
    for i in range(0,len(mids),80):
        blk=mids[i:i+80];q=','.join('?'*len(blk))
        sql=f"select {','.join(cols)} from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms"
        for r in c.execute(sql,blk): out.setdefault(int(r['market_id']),[]).append((int(r['sampled_at_ms']),dict(r)))
    c.close();return out

def main():
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
        if not arr: vals.append(None);continue
        times=[x[0] for x in arr];j=bisect.bisect_left(times,int(r.first_event_ms))-1
        if j<0: vals.append(None);continue
        t,z=arr[j];lag=int(r.first_event_ms)-t
        if lag<0 or lag>1500: vals.append(None);continue
        side=str(r.weak_side);b=z['predict_up_bid'] if side=='UP' else z['predict_down_bid'];ask=z['predict_up_ask'] if side=='UP' else z['predict_down_ask'];mid=z['predict_up_mid'] if side=='UP' else z['predict_down_mid']
        allv=[b,ask,mid]+[z[k] for k in MICRO]
        if not all(finite(x) for x in allv): vals.append(None);continue
        vals.append((lag,float(b),float(ask),float(mid),*[float(z[k]) for k in MICRO]))
    d['_p']=vals;d=d[d._p.notna()].copy()
    d['public_lag_ms']=d._p.map(lambda x:x[0]);d['weak_bid']=d._p.map(lambda x:x[1]);d['weak_ask']=d._p.map(lambda x:x[2]);d['weak_mid']=d._p.map(lambda x:x[3])
    for i,k in enumerate(MICRO,start=4): d[k]=d._p.map(lambda x,j=i:x[j])
    d.drop(columns=['_p'],inplace=True)
    d['weak_spread']=d.weak_ask-d.weak_bid;d['weak_bid_cheapness']=d.weak_mid-d.weak_bid;d['weak_ask_premium']=d.weak_ask-d.weak_mid;d['weak_completion_payoff_bid']=1-d.weak_bid;d['y']=d.y.astype(int)
    ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];n=len(ordered);test=8;starts=[24,32,40];starts=[x for x in starts if x+test<=n]
    folds=[]
    for fi,st in enumerate(starts):
        tr=d[d.market_id.isin(ordered[:st])].copy();te=d[d.market_id.isin(ordered[st:st+test])].copy()
        if tr.y.nunique()<2: continue
        q1,q2=np.quantile(tr.belief_placement_readiness_5s,[1/3,2/3])
        mb=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=1038+fi).fit(tr[BASELINE],tr.y)
        mm=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=1038+fi).fit(tr[BASELINE+MICRO],tr.y)
        pb=mb.predict_proba(te[BASELINE])[:,1];pm=mm.predict_proba(te[BASELINE+MICRO])[:,1];te=te.copy();te['_pb']=pb;te['_pm']=pm
        mask=te.belief_placement_readiness_5s>q2;z=te[mask]
        if len(z)>=30 and z.y.nunique()>1:
            bm=met(z.y,z._pb);cm=met(z.y,z._pm);res={'eligible':True,'BASELINE':bm,'PLUS_MARKET_MICRO':cm,'increment':{'deltaAuc':cm['auc']-bm['auc'],'deltaAp':cm['ap']-bm['ap'],'logLossImprovement':bm['logLoss']-cm['logLoss']}}
        else: res={'eligible':False,'n':int(len(z)),'rate':float(z.y.mean()) if len(z) else None}
        folds.append({'fold':fi,'trainMarkets':st,'testMarkets':test,'trainRows':int(len(tr)),'testRows':int(len(te)),'trainReadinessTertiles':[float(q1),float(q2)],'HIGH':res})
    inc=[f['HIGH']['increment'] for f in folds if f['HIGH'].get('eligible')]
    mean=lambda k: float(np.mean([x[k] for x in inc])) if inc else None
    keep=bool(len(inc)>=3 and all(x['deltaAuc']>=0 for x in inc) and mean('deltaAuc')>=.01 and mean('deltaAp')>0 and mean('logLossImprovement')>0)
    status='TESTED_KEEP_SIGNAL' if keep else ('TESTED_INCONCLUSIVE' if len(inc)<3 else 'TESTED_REJECTED')
    variation={k:{'std':float(d[k].std()),'p10':float(d[k].quantile(.1)),'p50':float(d[k].median()),'p90':float(d[k].quantile(.9))} for k in MICRO}
    art={'version':'R4_MARKET_PARTICIPATION_MICRO_HIGH_PREPARE_TAIL_ROUTING_V1','testId':TEST_ID,'status':status,'researchOnly':True,'actionAuthority':False,'semanticAxis':'FORMATION_INFORMATION_ROUTING / MARKET_PARTICIPATION_MICRO_IN_HIGH_PREPARE_TAIL_CONTEXT','layerAssignment':{'spotFuturesReturnsQueueTaker':'INFORMATION_MARKET_PARTICIPATION_CONTEXT','weakSideQuoteEconomics':'INFORMATION_RETAINED_CONTEXT','placementReadiness':'BELIEF_PREPARE_PARTICIPATION_CONTEXT','predictStrike':'BELIEF_FORMATION_DIRECTIONAL_CONTEXT','portfolioPayoffGeometry':'LOGIC_RESPONSIBILITY_STATE','output':'TAIL_INFORMATION_ROUTING_CONTEXT_ONLY_NOT_ACTION_AUTHORITY'},'question':'Does strict-past spot/futures market-participation micro add 15s floor-tail information inside HIGH PREPARE above retained quote+geometry+Predict/strike context?','label':'future15_min(pre_worst_case_pnl) < current; future Target trajectory offline only','coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'damageRate':float(d.y.mean()),'medianPublicLagMs':float(d.public_lag_ms.median()),'maxPublicLagMs':float(d.public_lag_ms.max())},'features':{'BASELINE':BASELINE,'MARKET_MICRO':MICRO},'microVariation':variation,'folds':folds,'primary':{'eligibleFolds':len(inc),'meanDeltaAuc':mean('deltaAuc'),'meanDeltaAp':mean('deltaAp'),'meanLogLossImprovement':mean('logLossImprovement'),'worstDeltaAuc':float(min(x['deltaAuc'] for x in inc)) if inc else None,'allFoldAucNonnegative':all(x['deltaAuc']>=0 for x in inc) if inc else False},'fixedKeepRule':'At least 3 eligible HIGH_PREPARE chronological folds; all deltaAUC>=0; mean deltaAUC>=0.01; mean deltaAP>0; mean log-loss improvement>0.','guards':['strict-past public snapshot <=1500ms','future Target only offline label','winner/settlement/future fill excluded','no threshold/model/hyperparameter sweep','no action authority','no Echtgeld fit/ingest','2026-08-16 excluded by canonical source cohort','0-60s excluded']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':art['coverage'],'primary':art['primary'],'foldHigh':[f['HIGH'] for f in folds]},ensure_ascii=False))
if __name__=='__main__': main()
