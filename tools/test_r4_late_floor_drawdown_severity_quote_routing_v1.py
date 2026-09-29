from __future__ import annotations
import bisect,json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
DB=ROOT/'data/public_research_archive_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/r4_late_floor_drawdown_severity_quote_routing_v1.json'
CTX=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_worst_case_pnl','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant']
QUOTE=['weak_bid','weak_ask','weak_mid','weak_spread','weak_bid_cheapness','weak_ask_premium','weak_completion_payoff_bid']

def hgb(seed):
    return HistGradientBoostingRegressor(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,min_samples_leaf=25,random_state=seed,loss='squared_error')

def met(y,p):
    y=np.asarray(y,float);p=np.asarray(p,float);ae=np.abs(y-p)
    rho=spearmanr(y,p).statistic if len(y)>=3 and np.nanstd(y)>0 and np.nanstd(p)>0 else np.nan
    return {'n':int(len(y)),'meanTarget':float(np.mean(y)) if len(y) else None,'medianTarget':float(np.median(y)) if len(y) else None,'spearman':float(rho) if np.isfinite(rho) else None,'mae':float(mean_absolute_error(y,p)) if len(y) else None,'p90AbsError':float(np.quantile(ae,.90)) if len(y) else None}

def load_public(mids):
    c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');out={}
    for i in range(0,len(mids),80):
        blk=mids[i:i+80];q=','.join('?'*len(blk))
        for r in c.execute(f'''select market_id,sampled_at_ms,predict_up_bid,predict_up_ask,predict_up_mid,predict_down_bid,predict_down_ask,predict_down_mid from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms''',blk):
            out.setdefault(int(r['market_id']),[]).append((int(r['sampled_at_ms']),dict(r)))
    c.close();return out

def attach_label(a):
    labs=[]
    for mid,g in a.groupby('market_id',sort=False):
        g=g.sort_values('first_event_ms');ts=g.first_event_ms.astype('int64').to_numpy();fl=g.pre_worst_case_pnl.astype(float).to_numpy();idx=g.index.to_numpy()
        for k,ix in enumerate(idx):
            hi=np.searchsorted(ts,ts[k]+10000,side='right');fut=fl[k+1:hi]
            labs.append((ix,np.nan if len(fut)==0 else max(0.0,float(fl[k]-np.nanmin(fut)))))
    return a.join(pd.DataFrame(labs,columns=['idx','drawdown10s']).set_index('idx'))

def main():
    f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan).sort_values(['market_id','first_event_ms']).copy()
    need=CTX+['market_id','first_event_ms','weak_side']
    f=f.dropna(subset=need).copy();f['market_id']=f.market_id.astype(int)
    f=attach_label(f)
    d=f[(f.drawdown10s.notna())&(f.seconds_left>0)&(f.seconds_left<=60)&(f.pre_worst_case_pnl>0)].copy()
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
    d['weak_spread']=d.weak_ask-d.weak_bid;d['weak_bid_cheapness']=d.weak_mid-d.weak_bid;d['weak_ask_premium']=d.weak_ask-d.weak_mid;d['weak_completion_payoff_bid']=1-d.weak_bid
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
        if len(te)<20 or tr.drawdown10s.nunique()<2 or te.drawdown10s.nunique()<2: continue
        mb=hgb(7100+fi).fit(tr[CTX],tr.drawdown10s);mq=hgb(7200+fi).fit(tr[CTX+QUOTE],tr.drawdown10s)
        pb=np.maximum(0.,mb.predict(te[CTX]));pq=np.maximum(0.,mq.predict(te[CTX+QUOTE]))
        bm=met(te.drawdown10s,pb);qm=met(te.drawdown10s,pq)
        if bm['spearman'] is None or qm['spearman'] is None: continue
        folds.append({'fold':fi,'trainMarkets':st,'testMarkets':test,'trainRows':int(len(tr)),'testRows':int(len(te)),'BASE':bm,'PLUS_QUOTE_ECON':qm,'increment':{'deltaSpearman':qm['spearman']-bm['spearman'],'maeImprovement':bm['mae']-qm['mae'],'p90AbsErrorImprovement':bm['p90AbsError']-qm['p90AbsError']}})
    inc=[x['increment'] for x in folds]
    mean=lambda k: float(np.mean([z[k] for z in inc])) if inc else None
    testrows=sum(x['testRows'] for x in folds)
    support_ok=len(folds)>=3 and testrows>=300
    qualifies=bool(support_ok and all(z['deltaSpearman']>=0 for z in inc) and mean('deltaSpearman')>=.03 and mean('maeImprovement')>0 and mean('p90AbsErrorImprovement')>=0)
    status='TESTED_KEEP_SIGNAL' if qualifies else ('TESTED_INCONCLUSIVE' if not support_ok else 'TESTED_REJECTED')
    art={'version':'R4_LATE_FLOOR_DRAWDOWN_SEVERITY_QUOTE_ROUTING_V1','testId':'R4_LATE_FLOOR_DRAWDOWN_SEVERITY_QUOTE_ROUTING_V1_20260827_1134','status':status,'researchOnly':True,'actionAuthority':False,'layerAssignment':{'weakSidePublicQuoteEconomics':'INFORMATION','predictStrike':'BELIEF_CONTEXT','portfolioPayoffGeometry':'LOGIC_STATE_CONTEXT','output':'LATE_CROSSING_PROTECTION_SEVERITY_INFORMATION_CONTEXT_NOT_ACTION_AUTHORITY'},'question':'In final 0-60s positive-floor states, does strict-past weak-side quote economics add stable information beyond geometry+Predict/strike for the magnitude of next-10s floor drawdown?','label':'drawdown10s=max(0,current pre_worst_case_pnl - minimum future pre_worst_case_pnl within 10s); future Target trajectory offline label only','coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'meanDrawdown10s':float(d.drawdown10s.mean()) if len(d) else None,'medianDrawdown10s':float(d.drawdown10s.median()) if len(d) else None,'p90Drawdown10s':float(d.drawdown10s.quantile(.9)) if len(d) else None,'medianPublicLagMs':float(d.public_lag_ms.median()) if len(d) else None,'maxPublicLagMs':float(d.public_lag_ms.max()) if len(d) else None},'folds':folds,'summary':{'eligibleFolds':len(folds),'totalTestRows':testrows,'meanDeltaSpearman':mean('deltaSpearman'),'worstFoldDeltaSpearman':min([z['deltaSpearman'] for z in inc]) if inc else None,'meanMaeImprovement':mean('maeImprovement'),'meanP90AbsErrorImprovement':mean('p90AbsErrorImprovement'),'allFoldSpearmanNonnegative':all(z['deltaSpearman']>=0 for z in inc) if inc else False,'qualifies':qualifies},'fixedKeepRule':'KEEP_SIGNAL only if >=3 eligible chronological folds, each fold deltaSpearman>=0, mean deltaSpearman>=0.03, mean MAE improvement>0, mean P90 absolute-error improvement>=0, and >=300 total test rows.','guards':['strict-past public quote <=1500ms','final 0-60s only','current positive floor only','future Target trajectory label only','winner/settlement excluded','no threshold/model/hyperparameter sweep','no Echtgeld fit/ingest','information context only; no action authority','Formation PREPARE authority excluded']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':art['coverage'],'summary':art['summary']},ensure_ascii=False))
if __name__=='__main__':main()
