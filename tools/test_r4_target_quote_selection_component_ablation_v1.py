from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
# reuse joined dataset logic by importing pilot module? simpler: execute helper by reading its artifact source not rows; rebuild by import impossible. Inline invoke pilot prep via runpy is awkward.
# This script imports functions/source constants and repeats minimal prep through exec of module namespace.
import importlib.util
spec=importlib.util.spec_from_file_location('pilot',ROOT/'tools/test_r4_target_quote_selection_residual_pilot_v1.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
SRC=mod.SRC;DB=mod.DB;OUT=ROOT/'data/research/r4_v0/hourly/r4_target_quote_selection_component_ablation_v1.json'
BASE=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','weak_predict_mid','weak_bid','weak_ask','weak_mid']
COMPS={'PLUS_ACTION_MINUS_BID':['action_minus_bid'],'PLUS_ASK_MINUS_ACTION':['ask_minus_action'],'PLUS_ACTION_MINUS_MID':['action_minus_mid'],'PLUS_ABS_DEV':['abs_action_minus_mid'],'PLUS_INSIDE':['action_inside_spread'],'PLUS_BID_AND_ASK_RESID':['action_minus_bid','ask_minus_action'],'PLUS_ALL':['action_minus_bid','ask_minus_action','action_minus_mid','abs_action_minus_mid','action_inside_spread']}
def metric(y,p):return {'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'ll':float(log_loss(y,p,labels=[0,1]))}
def fit(tr,te,fs,seed):
 m=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=seed).fit(tr[fs],tr.y);return metric(te.y,m.predict_proba(te[fs])[:,1])
def prep():
 import bisect,sqlite3
 d=pd.read_csv(SRC).sort_values(['first_event_ms','market_id']);d=d[d['mode'].astype(str).eq('REPAIR')].copy();d['y']=0
 for mid,g in d.groupby('market_id',sort=False):
  idx=g.index.to_list();ts=g.first_event_ms.astype('int64').to_numpy();nxt=np.r_[ts[1:],np.iinfo(np.int64).max];d.loc[idx,'y']=((nxt>ts)&(nxt<=ts+5000)).astype(int)
 mids=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index][:96];d=d[d.market_id.isin(mids)].copy();c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');pub={}
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
 d['_p']=vs;d=d[d._p.notna()].copy();d['weak_bid']=d._p.map(lambda x:x[1]);d['weak_ask']=d._p.map(lambda x:x[2]);d['weak_mid']=d._p.map(lambda x:x[3]);d['weak_predict_mid']=d.weak_mid;d.drop(columns=['_p'],inplace=True);d['action_minus_bid']=d.price-d.weak_bid;d['ask_minus_action']=d.weak_ask-d.price;d['action_minus_mid']=d.price-d.weak_mid;d['abs_action_minus_mid']=abs(d.action_minus_mid);d['action_inside_spread']=((d.price>=d.weak_bid)&(d.price<=d.weak_ask)).astype(int);return d
def main():
 d=prep();ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];n=len(ordered);test=max(8,min(16,n//5));starts=sorted(set(s for s in [max(32,int(n*.45)),max(40,int(n*.60)),max(48,int(n*.75))] if s+test<=n));folds=[]
 for fi,s in enumerate(starts):
  tr=d[d.market_id.isin(ordered[:s])];te=d[d.market_id.isin(ordered[s:s+test])];b=fit(tr,te,BASE,300+fi);r={'fold':fi,'base':b,'models':{}}
  for k,add in COMPS.items():
   x=fit(tr,te,BASE+add,300+fi);r['models'][k]={**x,'deltaAuc':x['auc']-b['auc'],'deltaAp':x['ap']-b['ap'],'llImprovement':b['ll']-x['ll']}
  folds.append(r)
 summary={}
 for k in COMPS:
  xs=[f['models'][k] for f in folds];summary[k]={'meanDeltaAuc':float(np.mean([x['deltaAuc'] for x in xs])),'meanDeltaAp':float(np.mean([x['deltaAp'] for x in xs])),'meanLlImprovement':float(np.mean([x['llImprovement'] for x in xs])),'allAucPositive':all(x['deltaAuc']>0 for x in xs),'allApPositive':all(x['deltaAp']>0 for x in xs),'allLlPositive':all(x['llImprovement']>0 for x in xs)}
 desc={}
 for y,g in d.groupby('y'):
  desc[str(int(y))]={'n':int(len(g)),'medianActionMinusBid':float(g.action_minus_bid.median()),'medianAskMinusAction':float(g.ask_minus_action.median()),'medianActionMinusMid':float(g.action_minus_mid.median()),'insideSpreadRate':float(g.action_inside_spread.mean()),'medianPrice':float(g.price.median())}
 art={'version':'R4_TARGET_QUOTE_SELECTION_COMPONENT_ABLATION_V1','researchOnly':True,'actionAuthority':False,'coverage':{'markets':int(d.market_id.nunique()),'rows':len(d),'positiveRate':float(d.y.mean())},'summary':summary,'descriptive':desc,'folds':folds};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'descriptive':desc},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
