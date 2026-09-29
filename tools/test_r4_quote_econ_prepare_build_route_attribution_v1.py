from __future__ import annotations
import bisect,json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
READINESS=ROOT/'data/research/r4_v0/hourly/r4_information_belief_state_v0_scores.csv'
DB=ROOT/'data/public_research_archive_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/r4_quote_econ_prepare_build_route_attribution_v1.json'
CTX=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_worst_case_pnl','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant']
BELIEF_BASE=CTX+['p_build','p_prepare']
QUOTE=['weak_bid','weak_ask','weak_mid','weak_spread','weak_bid_cheapness','weak_ask_premium','weak_completion_payoff_bid']

def hgb(seed):
    return HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,min_samples_leaf=25,random_state=seed)

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

def attach_tail_label(a):
    labs=[]
    for mid,g in a.groupby('market_id',sort=False):
        g=g.sort_values('first_event_ms');ts=g.first_event_ms.astype('int64').to_numpy();fl=g.pre_worst_case_pnl.astype(float).to_numpy();idx=g.index.to_numpy()
        for k,ix in enumerate(idx):
            hi=np.searchsorted(ts,ts[k]+15000,side='right');fut=fl[k+1:hi]
            labs.append((ix,np.nan if len(fut)==0 else int(np.nanmin(fut)<fl[k]-1e-12)))
    return a.join(pd.DataFrame(labs,columns=['idx','y']).set_index('idx'))

def main():
    f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan).sort_values(['market_id','first_event_ms']).copy()
    r=pd.read_csv(READINESS)[['market_id','first_event_ms','belief_placement_readiness_5s']].groupby(['market_id','first_event_ms'],as_index=False).mean()
    f=f.merge(r,on=['market_id','first_event_ms'],how='inner')
    need=CTX+['belief_placement_readiness_5s','market_id','first_event_ms','side','weak_side','is_add','mode']
    f=f.dropna(subset=need).copy();f['market_id']=f.market_id.astype(int)
    # Existing current-stack semantic labels: BUILD is the non-ADD/weak-side repair state; PREPARE is a next <=5s weak-side event.
    g=f.groupby('market_id');f['next_ms']=g.first_event_ms.shift(-1);f['next_side']=g.side.shift(-1)
    f=f[f.next_ms.notna()].copy();f['dt']=f.next_ms-f.first_event_ms
    f['prepare_weak5']=((f.dt>0)&(f.dt<=5000)&(f.next_side==f.weak_side)).astype(int)
    f['build']=(f.is_add.astype(int)==0).astype(int)
    f=attach_tail_label(f)
    # Tail-routing evaluation stays in the same local Formation REPAIR support as the parent test.
    d=f[(f['mode'].astype(str)=='REPAIR') & f.y.notna() & (f.seconds_left>60) & (f.seconds_left<=300)].copy()
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
    ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];starts=[24,32,40];test=8;starts=[x for x in starts if x+test<=len(ordered)]
    folds=[]
    for fi,st in enumerate(starts):
        tr_ids=set(ordered[:st]);te_ids=set(ordered[st:st+test])
        # Fit belief heads on all available strict-past Formation rows for the training markets.
        btr=f[f.market_id.isin(tr_ids) & (f.seconds_left>60) & (f.seconds_left<=300)].copy()
        if btr.build.nunique()<2 or btr.prepare_weak5.nunique()<2: continue
        mb=hgb(5100+fi).fit(btr[CTX],btr.build)
        mp=hgb(5200+fi).fit(btr[CTX+['belief_placement_readiness_5s']],btr.prepare_weak5)
        tr=d[d.market_id.isin(tr_ids)].copy();te=d[d.market_id.isin(te_ids)].copy()
        tr['p_build']=mb.predict_proba(tr[CTX])[:,1];te['p_build']=mb.predict_proba(te[CTX])[:,1]
        tr['p_prepare']=mp.predict_proba(tr[CTX+['belief_placement_readiness_5s']])[:,1];te['p_prepare']=mp.predict_proba(te[CTX+['belief_placement_readiness_5s']])[:,1]
        if tr.y.nunique()<2: continue
        qb=float(np.quantile(tr.p_build,2/3));qp=float(np.quantile(tr.p_prepare,2/3))
        mt=hgb(5300+fi).fit(tr[BELIEF_BASE],tr.y);mq=hgb(5400+fi).fit(tr[BELIEF_BASE+QUOTE],tr.y)
        te['_pb']=mt.predict_proba(te[BELIEF_BASE])[:,1];te['_pq']=mq.predict_proba(te[BELIEF_BASE+QUOTE])[:,1]
        masks={
            'PREPARE_ONLY':(te.p_prepare>qp)&(te.p_build<=qb),
            'BUILD_ONLY':(te.p_build>qb)&(te.p_prepare<=qp),
            'BOTH_HIGH':(te.p_build>qb)&(te.p_prepare>qp),
            'NEITHER_HIGH':(te.p_build<=qb)&(te.p_prepare<=qp)
        }
        regs={}
        for nm,mask in masks.items():
            z=te[mask]
            if len(z)>=30 and z.y.nunique()>1:
                bm=met(z.y,z._pb);qm=met(z.y,z._pq);regs[nm]={'BASE':bm,'PLUS_QUOTE_ECON':qm,'increment':{'deltaAuc':qm['auc']-bm['auc'],'deltaAp':qm['ap']-bm['ap'],'logLossImprovement':bm['logLoss']-qm['logLoss']}}
            else: regs[nm]={'n':int(len(z)),'eligible':False,'rate':float(z.y.mean()) if len(z) else None}
        folds.append({'fold':fi,'trainMarkets':st,'testMarkets':test,'trainRows':int(len(tr)),'testRows':int(len(te)),'trainHighThresholds':{'p_build':qb,'p_prepare':qp},'regimes':regs})
    routes=['PREPARE_ONLY','BUILD_ONLY','BOTH_HIGH'];summary={}
    for route in routes:
        inc=[x['regimes'][route]['increment'] for x in folds if 'increment' in x['regimes'].get(route,{})]
        mean=lambda k: float(np.mean([z[k] for z in inc])) if inc else None
        qualifies=bool(len(inc)>=3 and all(z['deltaAuc']>=0 for z in inc) and mean('deltaAuc')>=.01 and mean('deltaAp')>0 and mean('logLossImprovement')>0)
        summary[route]={'eligibleFolds':len(inc),'meanDeltaAuc':mean('deltaAuc'),'meanDeltaAp':mean('deltaAp'),'meanLogLossImprovement':mean('logLossImprovement'),'allFoldAucNonnegative':all(z['deltaAuc']>=0 for z in inc) if inc else False,'qualifies':qualifies,'foldDeltaAuc':[z['deltaAuc'] for z in inc]}
    q=[k for k,v in summary.items() if v['qualifies']]
    attribution='NONE'
    if 'BOTH_HIGH' in q:
        both=summary['BOTH_HIGH']['meanDeltaAuc'];sing=[summary[x]['meanDeltaAuc'] for x in ('PREPARE_ONLY','BUILD_ONLY') if summary[x]['meanDeltaAuc'] is not None]
        if sing and both>=max(sing)+.01: attribution='PARALLEL_PREPARE_BUILD_INTERACTION'
    if attribution=='NONE' and len(q)==1 and q[0] in ('PREPARE_ONLY','BUILD_ONLY'):
        r0=q[0];other='BUILD_ONLY' if r0=='PREPARE_ONLY' else 'PREPARE_ONLY';ov=summary[other]['meanDeltaAuc']
        if ov is None or summary[r0]['meanDeltaAuc']>=ov+.01: attribution=r0
    if attribution=='NONE' and q: attribution='BROAD_OR_AMBIGUOUS_ROUTING'
    support_ok=all(summary[x]['eligibleFolds']>=3 for x in routes)
    status='TESTED_KEEP_SIGNAL' if q else ('TESTED_INCONCLUSIVE' if not support_ok else 'TESTED_REJECTED')
    art={'version':'R4_QUOTE_ECON_PREPARE_BUILD_ROUTE_ATTRIBUTION_V1','testId':'R4_QUOTE_ECON_PREPARE_BUILD_ROUTE_ATTRIBUTION_V1_20260827_0536','status':status,'researchOnly':True,'actionAuthority':False,'layerAssignment':{'weakSidePublicQuoteEconomics':'INFORMATION','placementReadinessPrepare':'BELIEF_PREPARE','portableBuild':'BELIEF_BUILD','portfolioPredictStrike':'LOGIC_STATE_CONTEXT','output':'INFORMATION_ROUTING_DIAGNOSTIC_NOT_ACTION_AUTHORITY'},'question':'Does the prior conditional quote-economics tail signal route specifically to PREPARE-only, BUILD-only, or BOTH-high parallel-belief states?','label':'next-15s future minimum pre_worst_case_pnl < current; Target future trajectory offline label only','coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'damageRate':float(d.y.mean()),'medianPublicLagMs':float(d.public_lag_ms.median()),'maxPublicLagMs':float(d.public_lag_ms.max())},'folds':folds,'routeSummary':summary,'qualifyingRoutes':q,'attribution':attribution,'fixedKeepRule':'Route qualifies with >=3 eligible folds, all deltaAUC>=0, mean deltaAUC>=0.01, mean deltaAP>0, mean log-loss improvement>0. Interaction attribution additionally requires BOTH_HIGH mean deltaAUC >= each single route by 0.01; single-route attribution requires a unique qualifying single route and >=0.01 mean deltaAUC advantage over the other single route.','guards':['strict-past public quote <=1500ms','60-300s Formation only','future Target trajectory label only','winner/settlement excluded','no threshold/model/hyperparameter sweep','no Echtgeld fit/ingest','information routing only; no action authority','0-60s excluded']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':art['coverage'],'summary':summary,'qualifyingRoutes':q,'attribution':attribution},ensure_ascii=False))
if __name__=='__main__': main()
