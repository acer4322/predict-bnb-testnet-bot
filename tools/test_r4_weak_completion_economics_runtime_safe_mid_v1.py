from __future__ import annotations
import bisect, json, sqlite3
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/target_strike_distance_formation_mode_rebuild_v2_rows.csv'
DB=ROOT/'data/public_research_archive_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/r4_weak_completion_economics_runtime_safe_mid_v1.json'

BASE=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant']
SIDE=['weak_predict_mid']
PUBLIC_PRICE=['weak_bid','weak_ask','weak_mid','weak_spread','weak_bid_cheapness','weak_ask_premium','weak_completion_payoff_bid']
INTER=['weak_bid_x_gap','weak_cheap_x_gap','weak_cheap_x_conf']

def metric(y,p):
 return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def fit_eval(tr,te,fs,seed):
 m=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=seed).fit(tr[fs],tr.y)
 return metric(te.y.to_numpy(),m.predict_proba(te[fs])[:,1])

def load_public(mids):
 c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True); c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
 out={}
 for i in range(0,len(mids),80):
  blk=mids[i:i+80]; q=','.join('?'*len(blk))
  rows=c.execute(f'''select market_id,sampled_at_ms,predict_up_bid,predict_up_ask,predict_up_mid,predict_down_bid,predict_down_ask,predict_down_mid from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms''',blk).fetchall()
  for r in rows:
   mid=int(r['market_id']);out.setdefault(mid,[]).append((int(r['sampled_at_ms']),dict(r)))
 c.close(); return out

def main():
 d=pd.read_csv(SRC).sort_values(['first_event_ms','market_id']).copy()
 d=d[d['mode'].astype(str).eq('REPAIR')].copy()
 # label: another REPAIR parent begins within 5s in same market
 d['y']=0
 for mid,g in d.groupby('market_id',sort=False):
  idx=g.index.to_list();ts=g.first_event_ms.astype('int64').to_numpy();nxt=np.r_[ts[1:],np.iinfo(np.int64).max]
  d.loc[idx,'y']=((nxt>ts)&(nxt<=ts+5000)).astype(int)
 mids=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index]
 # bounded mid-scale: first 96 chronological markets if available
 mids=mids[:min(96,len(mids))]; d=d[d.market_id.isin(mids)].copy()
 pub=load_public(mids)
 vals=[]
 for r in d.itertuples(index=False):
  arr=pub.get(int(r.market_id),[])
  if not arr: vals.append(None); continue
  times=[x[0] for x in arr];j=bisect.bisect_left(times,int(r.first_event_ms))-1
  if j<0: vals.append(None);continue
  t,z=arr[j]; lag=int(r.first_event_ms)-t
  if lag<0 or lag>1500: vals.append(None);continue
  side=str(r.weak_side)
  if side=='UP': b,a,m=z['predict_up_bid'],z['predict_up_ask'],z['predict_up_mid']
  else: b,a,m=z['predict_down_bid'],z['predict_down_ask'],z['predict_down_mid']
  if any(x is None for x in (b,a,m)): vals.append(None);continue
  vals.append((t,lag,float(b),float(a),float(m)))
 d['_p']=vals;d=d[d._p.notna()].copy()
 d['public_sample_ms']=d._p.map(lambda x:x[0]);d['public_lag_ms']=d._p.map(lambda x:x[1]);d['weak_bid']=d._p.map(lambda x:x[2]);d['weak_ask']=d._p.map(lambda x:x[3]);d['weak_mid']=d._p.map(lambda x:x[4]);d.drop(columns=['_p'],inplace=True)
 d['weak_predict_mid']=d['weak_mid'];d['weak_spread']=d.weak_ask-d.weak_bid;d['weak_bid_cheapness']=d.weak_mid-d.weak_bid;d['weak_ask_premium']=d.weak_ask-d.weak_mid;d['weak_completion_payoff_bid']=1-d.weak_bid
 d['weak_bid_x_gap']=d.weak_bid*d.pre_abs_payoff_gap;d['weak_cheap_x_gap']=d.weak_bid_cheapness*d.pre_abs_payoff_gap;d['weak_cheap_x_conf']=d.weak_bid_cheapness*d.predict_edge
 # fixed chronological 4 expanding blocks, no threshold sweep
 ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index]
 n=len(ordered); test=max(8,min(16,n//5)); starts=[max(32,int(n*.45)),max(40,int(n*.60)),max(48,int(n*.75))]
 starts=sorted(set(s for s in starts if s+test<=n))
 specs={'BASE':BASE,'BASE_SIDE':BASE+SIDE,'PLUS_PUBLIC_PRICE':BASE+SIDE+PUBLIC_PRICE,'PLUS_INTERACTIONS':BASE+SIDE+PUBLIC_PRICE+INTER}
 folds=[]
 for fi,s in enumerate(starts):
  tr=d[d.market_id.isin(ordered[:s])].copy();te=d[d.market_id.isin(ordered[s:s+test])].copy()
  rr={'fold':fi,'trainMarkets':s,'testMarkets':test,'trainRows':len(tr),'testRows':len(te),'testRate':float(te.y.mean()),'models':{}}
  for k,fs in specs.items(): rr['models'][k]=fit_eval(tr,te,fs,100+fi)
  b=rr['models']['BASE_SIDE'];x=rr['models']['PLUS_PUBLIC_PRICE'];z=rr['models']['PLUS_INTERACTIONS']
  rr['publicPriceIncrement']={'deltaAuc':x['auc']-b['auc'],'deltaAp':x['ap']-b['ap'],'logLossImprovement':b['logLoss']-x['logLoss']}
  rr['interactionIncrementVsBaseSide']={'deltaAuc':z['auc']-b['auc'],'deltaAp':z['ap']-b['ap'],'logLossImprovement':b['logLoss']-z['logLoss']}
  folds.append(rr)
 def avg(path):
  vals=[]
  for f in folds:
   cur=f
   for p in path: cur=cur[p]
   vals.append(cur)
  return float(np.mean(vals)) if vals else None
 art={'version':'R4_WEAK_COMPLETION_ECONOMICS_RUNTIME_SAFE_MID_V1','researchOnly':True,'actionAuthority':False,'question':'Do strict-past public weak-side quote economics add stable chronological information about Target continuing weak-side BUILD within 5s, beyond portfolio/phase/Predict/strike context?','coverage':{'markets':int(d.market_id.nunique()),'rows':len(d),'positiveRate':float(d.y.mean()),'medianPublicLagMs':float(d.public_lag_ms.median()),'maxPublicLagMs':float(d.public_lag_ms.max())},'features':specs,'folds':folds,'summary':{'publicPriceMeanDeltaAuc':avg(['publicPriceIncrement','deltaAuc']),'publicPriceMeanDeltaAp':avg(['publicPriceIncrement','deltaAp']),'publicPriceMeanLogLossImprovement':avg(['publicPriceIncrement','logLossImprovement']),'publicPriceAllFoldsAucPositive':all(f['publicPriceIncrement']['deltaAuc']>0 for f in folds),'publicPriceAllFoldsApPositive':all(f['publicPriceIncrement']['deltaAp']>0 for f in folds),'publicPriceAllFoldsLlPositive':all(f['publicPriceIncrement']['logLossImprovement']>0 for f in folds),'interactionMeanDeltaAuc':avg(['interactionIncrementVsBaseSide','deltaAuc']),'interactionAllFoldsAucPositive':all(f['interactionIncrementVsBaseSide']['deltaAuc']>0 for f in folds)},'guards':['Latest public snapshot strictly before Target parent first_event_ms.','Public lag <=1500ms.','Target action price is not a feature.','Future REPAIR is label only.','No winner/settlement.','No threshold/model sweep.','No action authority.'],'keepRule':'KEEP_SIGNAL for larger validation only if public quote economics improves AUC in every chronological fold and mean AUC/AP/log-loss directions are all favorable; otherwise REJECT/INCONCLUSIVE.'}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':art['summary'],'increments':[f['publicPriceIncrement'] for f in folds]},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
