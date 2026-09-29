from __future__ import annotations
import bisect,json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv';DB=ROOT/'data/public_research_archive_v1.db';OUT=ROOT/'data/research/r4_v0/hourly/r4_target_quote_commitment_information_role_v1.json'
FORMATION=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','weak_bid','weak_ask','weak_mid','weak_spread']
SPOT=['spot_queue_imbalance','spot_taker_imbalance_250ms','spot_taker_imbalance_1s','spot_return_250ms_bps','spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps']
FUT=['futures_queue_imbalance','futures_taker_imbalance_250ms','futures_taker_imbalance_1s','futures_return_250ms_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps','perp_spot_basis_bps']
def metric(y,p):return {'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'ll':float(log_loss(y,p,labels=[0,1]))}
def fit(tr,te,fs,seed):
 xtr=tr[fs].replace([np.inf,-np.inf],np.nan).fillna(0);xte=te[fs].replace([np.inf,-np.inf],np.nan).fillna(0);m=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=seed).fit(xtr,tr.y);return metric(te.y,m.predict_proba(xte)[:,1])
def main():
 d=pd.read_csv(SRC).sort_values(['first_event_ms','market_id']);d=d[d['mode'].astype(str).eq('REPAIR')].copy();mids=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index][:96];d=d[d.market_id.isin(mids)].copy()
 c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');pub={}
 for i in range(0,len(mids),80):
  blk=mids[i:i+80];q=','.join('?'*len(blk));rs=c.execute(f'''select market_id,sampled_at_ms,predict_up_bid,predict_up_ask,predict_up_mid,predict_down_bid,predict_down_ask,predict_down_mid from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms''',blk).fetchall()
  for r in rs:pub.setdefault(int(r['market_id']),[]).append((int(r['sampled_at_ms']),dict(r)))
 c.close();vs=[]
 for r in d.itertuples(index=False):
  arr=pub.get(int(r.market_id),[]);times=[x[0] for x in arr];j=bisect.bisect_left(times,int(r.first_event_ms))-1
  if j<0:vs.append(None);continue
  t,z=arr[j];lag=int(r.first_event_ms)-t
  if lag<0 or lag>1500:vs.append(None);continue
  pref='predict_up_' if str(r.weak_side)=='UP' else 'predict_down_';b,a,m=z[pref+'bid'],z[pref+'ask'],z[pref+'mid']
  if any(x is None for x in (b,a,m)):vs.append(None);continue
  vs.append((lag,float(b),float(a),float(m)))
 d['_p']=vs;d=d[d._p.notna()].copy();d['public_lag_ms']=d._p.map(lambda x:x[0]);d['weak_bid']=d._p.map(lambda x:x[1]);d['weak_ask']=d._p.map(lambda x:x[2]);d['weak_mid']=d._p.map(lambda x:x[3]);d['weak_spread']=d.weak_ask-d.weak_bid;d.drop(columns=['_p'],inplace=True)
 # semantic label inherited from quote geometry: at current public best bid or more aggressive inside spread vs behind bid
 d['y']=(d.price>=d.weak_bid-1e-9).astype(int)
 specs={'FORMATION_ONLY':FORMATION,'PLUS_SPOT_MICRO':FORMATION+SPOT,'PLUS_FUTURES_MICRO':FORMATION+FUT,'PLUS_ALL_MICRO':FORMATION+SPOT+FUT}
 ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];n=len(ordered);test=max(8,min(16,n//5));starts=sorted(set(s for s in [max(32,int(n*.45)),max(40,int(n*.60)),max(48,int(n*.75))] if s+test<=n));folds=[]
 for fi,s in enumerate(starts):
  tr=d[d.market_id.isin(ordered[:s])];te=d[d.market_id.isin(ordered[s:s+test])];rr={'fold':fi,'trainMarkets':s,'testMarkets':test,'testRate':float(te.y.mean()),'models':{}}
  for k,fs in specs.items():rr['models'][k]=fit(tr,te,fs,400+fi)
  b=rr['models']['FORMATION_ONLY'];
  for k in ['PLUS_SPOT_MICRO','PLUS_FUTURES_MICRO','PLUS_ALL_MICRO']:
   x=rr['models'][k];rr[k+'_INC']={'deltaAuc':x['auc']-b['auc'],'deltaAp':x['ap']-b['ap'],'llImprovement':b['ll']-x['ll']}
  folds.append(rr)
 summary={}
 for k in ['PLUS_SPOT_MICRO','PLUS_FUTURES_MICRO','PLUS_ALL_MICRO']:
  xs=[f[k+'_INC'] for f in folds];summary[k]={'meanDeltaAuc':float(np.mean([x['deltaAuc'] for x in xs])),'meanDeltaAp':float(np.mean([x['deltaAp'] for x in xs])),'meanLlImprovement':float(np.mean([x['llImprovement'] for x in xs])),'allAucPositive':all(x['deltaAuc']>0 for x in xs),'allApPositive':all(x['deltaAp']>0 for x in xs),'allLlPositive':all(x['llImprovement']>0 for x in xs)}
 desc=d.groupby('y').agg(n=('y','size'),median_price=('price','median'),median_bid=('weak_bid','median'),median_gap=('pre_abs_payoff_gap','median'),median_edge=('predict_edge','median')).reset_index().to_dict('records')
 art={'version':'R4_TARGET_QUOTE_COMMITMENT_INFORMATION_ROLE_V1','researchOnly':True,'actionAuthority':False,'layer':'EXECUTION_BELIEF_DIAGNOSTIC','question':'Which strict-past information sources predict whether Target expresses an already-existing weak-side REPAIR responsibility with an at-bid/inside-spread high-commitment Maker quote rather than a behind-bid quote?','coverage':{'markets':int(d.market_id.nunique()),'rows':len(d),'highCommitRate':float(d.y.mean()),'medianPublicLagMs':float(d.public_lag_ms.median())},'featureSets':specs,'summary':summary,'descriptive':desc,'folds':folds,'guards':['Label is Target quote posture, not final action mode.','Public quote is strictly before parent event, lag <=1500ms.','All external micro features are strict-past rows already stored in the Target attribution dataset.','No winner/future outcome.','No threshold sweep: high commitment is semantic at-or-above-current-best-bid vs behind-bid.','No action authority.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':summary,'descriptive':desc},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
