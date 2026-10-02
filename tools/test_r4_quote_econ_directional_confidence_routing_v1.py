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
OUT=ROOT/'data/research/r4_v0/hourly/r4_quote_econ_directional_confidence_routing_v1.json'
TEST_ID='R4_QUOTE_ECON_DIRECTIONAL_CONFIDENCE_ROUTING_V1_20260828_0834'
BASE=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_worst_case_pnl','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','belief_placement_readiness_5s']
QUOTE=['weak_bid','weak_ask','weak_mid','weak_spread','weak_bid_cheapness','weak_ask_premium','weak_completion_payoff_bid']

def met(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,float)
    return {'n':int(len(y)),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(y) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None}

def load_public(mids):
    c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');out={}
    for i in range(0,len(mids),80):
        blk=mids[i:i+80];q=','.join('?'*len(blk))
        for r in c.execute(f'''select market_id,sampled_at_ms,predict_up_bid,predict_up_ask,predict_up_mid,predict_down_bid,predict_down_ask,predict_down_mid from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms''',blk):
            out.setdefault(int(r['market_id']),[]).append((int(r['sampled_at_ms']),dict(r)))
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
    lm=pd.DataFrame(labs,columns=['idx','y']).set_index('idx');a=a.join(lm)
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
        if any(x is None for x in (b,ask,mid)): vals.append(None);continue
        vals.append((lag,float(b),float(ask),float(mid)))
    d['_p']=vals;d=d[d._p.notna()].copy();d['public_lag_ms']=d._p.map(lambda x:x[0]);d['weak_bid']=d._p.map(lambda x:x[1]);d['weak_ask']=d._p.map(lambda x:x[2]);d['weak_mid']=d._p.map(lambda x:x[3]);d.drop(columns=['_p'],inplace=True)
    d['weak_spread']=d.weak_ask-d.weak_bid;d['weak_bid_cheapness']=d.weak_mid-d.weak_bid;d['weak_ask_premium']=d.weak_ask-d.weak_mid;d['weak_completion_payoff_bid']=1-d.weak_bid;d['y']=d.y.astype(int);d['abs_predict_edge']=d.predict_edge.abs()
    ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];n=len(ordered);test=8;starts=[24,32,40];starts=[x for x in starts if x+test<=n]
    folds=[]
    for fi,st in enumerate(starts):
        tr=d[d.market_id.isin(ordered[:st])].copy();te=d[d.market_id.isin(ordered[st:st+test])].copy()
        if tr.y.nunique()<2: continue
        _,q2=np.quantile(tr.belief_placement_readiness_5s,[1/3,2/3])
        mb=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=880+fi).fit(tr[BASE],tr.y)
        mq=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=880+fi).fit(tr[BASE+QUOTE],tr.y)
        te=te.copy();te['_pb']=mb.predict_proba(te[BASE])[:,1];te['_pq']=mq.predict_proba(te[BASE+QUOTE])[:,1]
        high=te[te.belief_placement_readiness_5s>q2].copy()
        masks={'LOW':high.abs_predict_edge<.10,'MODERATE':(high.abs_predict_edge>=.10)&(high.abs_predict_edge<.30),'EXTREME':high.abs_predict_edge>=.30}
        regs={}
        for nm,mask in masks.items():
            z=high[mask]
            if len(z)>=20 and z.y.nunique()>1:
                bm=met(z.y,z._pb);qm=met(z.y,z._pq);regs[nm]={'eligible':True,'BASE':bm,'PLUS_QUOTE_ECON':qm,'increment':{'deltaAuc':qm['auc']-bm['auc'],'deltaAp':qm['ap']-bm['ap'],'logLossImprovement':bm['logLoss']-qm['logLoss']}}
            else: regs[nm]={'eligible':False,'n':int(len(z)),'rate':float(z.y.mean()) if len(z) else None,'positives':int(z.y.sum()) if len(z) else 0,'negatives':int(len(z)-z.y.sum()) if len(z) else 0}
        folds.append({'fold':fi,'trainMarkets':st,'testMarkets':test,'trainRows':len(tr),'testRows':len(te),'highPrepareCut':float(q2),'highPrepareRows':int(len(high)),'regimes':regs})
    mod=[f['regimes']['MODERATE']['increment'] for f in folds if f['regimes'].get('MODERATE',{}).get('eligible')]
    mean=lambda k: float(np.mean([x[k] for x in mod])) if mod else None
    keep=bool(len(mod)>=3 and all(x['deltaAuc']>=0 for x in mod) and mean('deltaAuc')>=.01 and mean('deltaAp')>0 and mean('logLossImprovement')>0)
    status='TESTED_KEEP_SIGNAL' if keep else ('TESTED_INCONCLUSIVE' if len(mod)<3 else 'TESTED_REJECTED')
    art={'version':'R4_QUOTE_ECON_DIRECTIONAL_CONFIDENCE_ROUTING_V1','testId':TEST_ID,'status':status,'researchOnly':True,'actionAuthority':False,
         'layerAssignment':{'weakSideQuoteEconomics':'INFORMATION','predictStrike':'FORMATION_BELIEF_DIRECTIONAL_CONTEXT','placementReadiness':'PREPARE_BELIEF_ROUTING_CONTEXT','portfolioPayoffGeometry':'LOGIC_STATE_CONTEXT','directionalConfidenceBin':'INFORMATION_ROUTING_CONTEXT','output':'FORMATION_TAIL_INFORMATION_CONTEXT_ONLY_NOT_ACTION_AUTHORITY'},
         'question':'Inside HIGH PREPARE, is the retained weak-side quote-economics information increment concentrated in the preregistered MODERATE abs(Predict edge) 0.10-0.30 directional-confidence regime?',
         'label':'future15_min(pre_worst_case_pnl) < current; future Target trajectory offline label only',
         'coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'damageRate':float(d.y.mean()),'medianPublicLagMs':float(d.public_lag_ms.median()),'maxPublicLagMs':float(d.public_lag_ms.max())},
         'confidenceBins':{'LOW':'abs(predict_edge)<0.10','MODERATE':'0.10<=abs(predict_edge)<0.30','EXTREME':'abs(predict_edge)>=0.30'},'features':{'BASE':BASE,'QUOTE_ECON':QUOTE},'folds':folds,
         'primaryModerateSummary':{'eligibleFolds':len(mod),'meanDeltaAuc':mean('deltaAuc'),'meanDeltaAp':mean('deltaAp'),'meanLogLossImprovement':mean('logLossImprovement'),'allFoldAucNonnegative':all(x['deltaAuc']>=0 for x in mod) if mod else False,'worstDeltaAuc':float(min(x['deltaAuc'] for x in mod)) if mod else None},
         'fixedKeepRule':'Primary MODERATE route: >=3 eligible chronological folds, each >=20 rows and both labels; every deltaAUC>=0; mean deltaAUC>=0.01; mean deltaAP>0; mean log-loss improvement>0. LOW/EXTREME cannot rescue failure.',
         'semanticDifference':'Parent test established HIGH PREPARE routing; prior attribution tested PREPARE vs BUILD heads. This test keeps HIGH PREPARE fixed and routes quote information by independently established directional-confidence regime only.',
         'guards':['strict-past public quote <=1500ms','future Target trajectory label only','winner/settlement excluded','2026-08-16 SEALED','no Echtgeld fit/ingest','no dream fill','no threshold/model/hyperparameter sweep','information context only; no action authority','live R3-S/R3.1/8781 untouched']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':art['coverage'],'primary':art['primaryModerateSummary'],'folds':[{'fold':f['fold'],'highPrepareRows':f['highPrepareRows'],'MODERATE':f['regimes']['MODERATE'],'LOW':f['regimes']['LOW'],'EXTREME':f['regimes']['EXTREME']} for f in folds]},ensure_ascii=False))
if __name__=='__main__': main()
