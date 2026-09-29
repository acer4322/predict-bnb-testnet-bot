from __future__ import annotations
import bisect,json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/target_strike_distance_formation_mode_rebuild_v2_rows.csv'
DB=ROOT/'data/public_research_archive_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/r4_target_quote_selection_residual_pilot_v1.json'
BASE=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','weak_predict_mid','weak_bid','weak_ask','weak_mid']
RES=['action_minus_bid','ask_minus_action','action_minus_mid','abs_action_minus_mid','action_inside_spread']

def met(y,p):
 return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)),'ll':float(log_loss(y,p,labels=[0,1]))}
def fit(tr,te,fs,seed):
 m=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=seed).fit(tr[fs],tr.y)
 return met(te.y.to_numpy(),m.predict_proba(te[fs])[:,1])
def main():
 d=pd.read_csv(SRC).sort_values(['first_event_ms','market_id']); d=d[d['mode'].astype(str).eq('REPAIR')].copy(); d['y']=0
 for mid,g in d.groupby('market_id',sort=False):
  idx=g.index.to_list();ts=g.first_event_ms.astype('int64').to_numpy();nxt=np.r_[ts[1:],np.iinfo(np.int64).max];d.loc[idx,'y']=((nxt>ts)&(nxt<=ts+5000)).astype(int)
 mids=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index][:96];d=d[d.market_id.isin(mids)].copy()
 c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');pub={}
 for i in range(0,len(mids),80):
  blk=mids[i:i+80];q=','.join('?'*len(blk));rs=c.execute(f'''select market_id,sampled_at_ms,predict_up_bid,predict_up_ask,predict_up_mid,predict_down_bid,predict_down_ask,predict_down_mid from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms''',blk).fetchall()
  for r in rs:pub.setdefault(int(r['market_id']),[]).append((int(r['sampled_at_ms']),dict(r)))
 c.close();arrs=[]
 for r in d.itertuples(index=False):
  arr=pub.get(int(r.market_id),[]);times=[x[0] for x in arr];j=bisect.bisect_left(times,int(r.first_event_ms))-1
  if j<0:arrs.append(None);continue
  t,z=arr[j];lag=int(r.first_event_ms)-t
  if lag<0 or lag>1500:arrs.append(None);continue
  side=str(r.weak_side);pref='predict_up_' if side=='UP' else 'predict_down_';b=z[pref+'bid'];a=z[pref+'ask'];m=z[pref+'mid']
  if any(x is None for x in (b,a,m)):arrs.append(None);continue
  arrs.append((lag,float(b),float(a),float(m)))
 d['_p']=arrs;d=d[d._p.notna()].copy();d['lag']=d._p.map(lambda x:x[0]);d['weak_bid']=d._p.map(lambda x:x[1]);d['weak_ask']=d._p.map(lambda x:x[2]);d['weak_mid']=d._p.map(lambda x:x[3]);d['weak_predict_mid']=d.weak_mid;d.drop(columns=['_p'],inplace=True)
 d['action_minus_bid']=d.price-d.weak_bid;d['ask_minus_action']=d.weak_ask-d.price;d['action_minus_mid']=d.price-d.weak_mid;d['abs_action_minus_mid']=abs(d.action_minus_mid);d['action_inside_spread']=((d.price>=d.weak_bid)&(d.price<=d.weak_ask)).astype(int)
 ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];n=len(ordered);test=max(8,min(16,n//5));starts=sorted(set(s for s in [max(32,int(n*.45)),max(40,int(n*.60)),max(48,int(n*.75))] if s+test<=n));folds=[]
 for fi,s in enumerate(starts):
  tr=d[d.market_id.isin(ordered[:s])];te=d[d.market_id.isin(ordered[s:s+test])];b=fit(tr,te,BASE,200+fi);x=fit(tr,te,BASE+RES,200+fi);folds.append({'fold':fi,'base':b,'plusResidual':x,'increment':{'deltaAuc':x['auc']-b['auc'],'deltaAp':x['ap']-b['ap'],'llImprovement':b['ll']-x['ll']}})
 inc=[f['increment'] for f in folds];art={'version':'R4_TARGET_QUOTE_SELECTION_RESIDUAL_PILOT_V1','researchOnly':True,'actionAuthority':False,'question':'Does Target quote-selection residual relative to strict-past public weak-side bid/ask predict continuation of weak-side BUILD, after public quote economics and Formation context are already controlled?','coverage':{'markets':int(d.market_id.nunique()),'rows':len(d),'positiveRate':float(d.y.mean()),'medianPublicLagMs':float(d.lag.median())},'folds':folds,'summary':{'meanDeltaAuc':float(np.mean([x['deltaAuc'] for x in inc])),'meanDeltaAp':float(np.mean([x['deltaAp'] for x in inc])),'meanLlImprovement':float(np.mean([x['llImprovement'] for x in inc])),'allAucPositive':all(x['deltaAuc']>0 for x in inc),'allApPositive':all(x['deltaAp']>0 for x in inc),'allLlPositive':all(x['llImprovement']>0 for x in inc)},'interpretation':'If stable positive, prior action-price signal is primarily quote-selection/execution-state information rather than raw completion economics. Target action price remains retrospective teacher evidence only, not runtime input.'};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':art['summary'],'increments':inc},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
