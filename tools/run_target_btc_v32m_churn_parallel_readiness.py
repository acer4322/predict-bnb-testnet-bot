from __future__ import annotations
import json, sqlite3, math, os
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
B=ROOT/'data/research/r4_v0/p0_provenance_v1'
PUB=ROOT/'data/public_source_snapshot_archive_v2.db'
MKR=ROOT/'data/wallet_maker_book_inference.db'
OFF=ROOT/'data/target_wallet_official_v1.db'
OUT=B/'TARGET_BTC_REPAIR_TAKER_CHURN_PARALLEL_READINESS_V32M_RESULT.json'
ROWS=B/'TARGET_BTC_REPAIR_TAKER_CHURN_PARALLEL_READINESS_V32M_ROWS.csv'
BASE=['seconds_left','spot_minus_strike_bps','abs_spot_minus_strike_bps','predict_up_mid','predict_down_mid','direction_score','abs_direction_score','spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps','spot_queue_imbalance','futures_queue_imbalance','spot_taker_imbalance_1s','futures_taker_imbalance_1s','pre_up_shares','pre_down_shares','pre_abs_net','pre_cost','pre_floor','pre_best_pnl','weak_side_up','weak_mid']
CHURN=['weak_cancel_count_1s','weak_cancel_count_3s','weak_cancel_count_10s','weak_reprice_count_1s','weak_reprice_count_3s','weak_reprice_count_10s','weak_same_price_refresh_count_3s','weak_same_price_refresh_count_10s','weak_candidate_starts_3s','weak_candidate_starts_10s','weak_churn_resting_ms_mean_10s','weak_churn_allocated_qty_10s']

def f(v):
 try:
  x=float(v); return x if math.isfinite(x) else np.nan
 except Exception:return np.nan

def snapv(j,k): return f(j.get(k))
def score(tr,va,te,fs):
 m=HistGradientBoostingClassifier(max_leaf_nodes=15,learning_rate=.05,max_iter=160,l2_regularization=1.0,random_state=32).fit(tr[fs].to_numpy(float),tr.label.to_numpy(int))
 out={}
 for nm,z in [('validation',va),('test',te)]:
  y=z.label.to_numpy(int); p=m.predict_proba(z[fs].to_numpy(float))[:,1]
  out[nm]={'rows':len(z),'positives':int(y.sum()),'rocAuc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p)),'brier':float(brier_score_loss(y,p))}
 return out

def main():
 cp=sqlite3.connect(f'file:{PUB.resolve().as_posix()}?mode=ro',uri=True); cp.row_factory=sqlite3.Row
 cm=sqlite3.connect(f'file:{MKR.resolve().as_posix()}?mode=ro',uri=True); cm.row_factory=sqlite3.Row
 co=sqlite3.connect(f'file:{OFF.resolve().as_posix()}?mode=ro',uri=True); co.row_factory=sqlite3.Row
 pm={int(x[0]) for x in cp.execute('select distinct market_id from public_source_snapshots_v2 where market_id>1399926')}
 vm={int(x[0]) for x in cm.execute('select distinct market_id from maker_book_inference_v21_market_meta where market_id>1399926')}
 om={int(x[0]) for x in co.execute("select distinct market_id from target_markets where asset='BTC' and market_id>1399926")}
 markets=sorted(pm&vm&om)[:60]
 rows=[]
 for mid in markets:
  # latest snapshot in each wall-clock second
  snaps=[]
  for rr in cp.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? order by sampled_at_ms,id',(mid,)):
   t=int(rr['sampled_at_ms']); b=(t//1000)*1000; j=json.loads(rr['snapshot_json'])
   if snaps and snaps[-1][0]==b: snaps[-1]=(b,t,j)
   else: snaps.append((b,t,j))
  ev=[dict(r) for r in co.execute("select event_ms,role,side,price,shares from wallet_shadow_target_events where market_id=? and asset='BTC' order by event_ms,id",(mid,))]
  tb=defaultdict(list)
  for e in ev:
   if e['role']=='TAKER': tb[(int(e['event_ms'])//1000)*1000].append(e['side'])
  canc=[dict(r) for r in cm.execute('select target_side,placement_source_ms,cancel_source_ms,resting_ms,allocated_quantity,post_action from maker_book_inference_v21_cancel_candidates where market_id=? order by cancel_source_ms',(mid,))]
  i=0; up=dn=cost=0.0
  for b,t,j in snaps:
   while i<len(ev) and int(ev[i]['event_ms'])<b:
    e=ev[i]; q=float(e['shares']); p=float(e['price']); cost+=p*q
    if e['side']=='UP': up+=q
    elif e['side']=='DOWN': dn+=q
    i+=1
   if abs(up-dn)<=1e-9: continue
   floor=min(up,dn)-cost
   if floor>=-1e-9: continue
   weak='UP' if up<dn else 'DOWN'; weakmid=snapv(j,'predictUpMid' if weak=='UP' else 'predictDownMid')
   wc=[x for x in canc if x['target_side']==weak]
   def ended(w): return [x for x in wc if t-w<=int(x['cancel_source_ms'])<t]
   c1,c3,c10=ended(1000),ended(3000),ended(10000)
   def rep(a): return sum(str(x.get('post_action') or '').startswith('REPRICE') for x in a)
   def same(a): return sum(str(x.get('post_action') or '')=='SAME_PRICE_REFRESH' for x in a)
   starts3=[x for x in wc if t-3000<=int(x['placement_source_ms'])<t]
   starts10=[x for x in wc if t-10000<=int(x['placement_source_ms'])<t]
   r={
    'market_id':mid,'bucket_ms':b,'sampled_at_ms':t,
    'seconds_left':snapv(j,'secondsLeft'),'spot_minus_strike_bps':snapv(j,'spotMinusStrikeBps'),'abs_spot_minus_strike_bps':abs(snapv(j,'spotMinusStrikeBps')) if math.isfinite(snapv(j,'spotMinusStrikeBps')) else np.nan,
    'predict_up_mid':snapv(j,'predictUpMid'),'predict_down_mid':snapv(j,'predictDownMid'),'direction_score':snapv(j,'directionScore'),'abs_direction_score':abs(snapv(j,'directionScore')) if math.isfinite(snapv(j,'directionScore')) else np.nan,
    'spot_return_1s_bps':snapv(j,'spotReturn1sBps'),'spot_return_3s_bps':snapv(j,'spotReturn3sBps'),'spot_return_5s_bps':snapv(j,'spotReturn5sBps'),'futures_return_1s_bps':snapv(j,'futuresReturn1sBps'),'futures_return_3s_bps':snapv(j,'futuresReturn3sBps'),'futures_return_5s_bps':snapv(j,'futuresReturn5sBps'),
    'spot_queue_imbalance':snapv(j,'spotQueueImbalance'),'futures_queue_imbalance':snapv(j,'futuresQueueImbalance'),'spot_taker_imbalance_1s':snapv(j,'spotTakerImbalance1s'),'futures_taker_imbalance_1s':snapv(j,'futuresTakerImbalance1s'),
    'pre_up_shares':up,'pre_down_shares':dn,'pre_abs_net':abs(up-dn),'pre_cost':cost,'pre_floor':floor,'pre_best_pnl':max(up,dn)-cost,'weak_side_up':1.0 if weak=='UP' else 0.0,'weak_mid':weakmid,
    'weak_cancel_count_1s':len(c1),'weak_cancel_count_3s':len(c3),'weak_cancel_count_10s':len(c10),'weak_reprice_count_1s':rep(c1),'weak_reprice_count_3s':rep(c3),'weak_reprice_count_10s':rep(c10),'weak_same_price_refresh_count_3s':same(c3),'weak_same_price_refresh_count_10s':same(c10),'weak_candidate_starts_3s':len(starts3),'weak_candidate_starts_10s':len(starts10),'weak_churn_resting_ms_mean_10s':float(np.mean([f(x['resting_ms']) for x in c10 if math.isfinite(f(x['resting_ms']))])) if any(math.isfinite(f(x['resting_ms'])) for x in c10) else np.nan,'weak_churn_allocated_qty_10s':float(sum(f(x['allocated_quantity']) for x in c10 if math.isfinite(f(x['allocated_quantity'])))),
    'label':1 if weak in tb.get(b+1000,[]) else 0
   }
   rows.append(r)
 cp.close(); cm.close(); co.close()
 df=pd.DataFrame(rows); mm=sorted(df.market_id.unique()); trm=mm[:36]; vam=mm[36:48]; tem=mm[48:60]
 tr=df[df.market_id.isin(trm)]; va=df[df.market_id.isin(vam)]; te=df[df.market_id.isin(tem)]
 bs=score(tr,va,te,BASE); cs=score(tr,va,te,BASE+CHURN)
 lift={'validationRocAuc':cs['validation']['rocAuc']-bs['validation']['rocAuc'],'validationAveragePrecision':cs['validation']['averagePrecision']-bs['validation']['averagePrecision'],'testRocAuc':cs['test']['rocAuc']-bs['test']['rocAuc'],'testAveragePrecision':cs['test']['averagePrecision']-bs['test']['averagePrecision'],'testBrierImprovement':bs['test']['brier']-cs['test']['brier']}
 keep=lift['validationRocAuc']>=0 and lift['validationAveragePrecision']>=0 and lift['testRocAuc']>=.02 and lift['testAveragePrecision']>=.01
 def rate(z): return {'rows':len(z),'positives':int(z.label.sum()),'rate':float(z.label.mean()) if len(z) else None}
 anatomy={}
 for name,mask in [('churn3_any',df.weak_cancel_count_3s>0),('no_churn3',df.weak_cancel_count_3s==0),('reprice3_any',df.weak_reprice_count_3s>0),('no_reprice3',df.weak_reprice_count_3s==0),('churn10_2plus',df.weak_cancel_count_10s>=2)]: anatomy[name]=rate(df[mask])
 time=[]
 for lo,hi in [(240,301),(180,240),(120,180),(60,120),(30,60),(0,30)]:
  z=df[(df.seconds_left>=lo)&(df.seconds_left<hi)]; time.append({'secondsLeft':[lo,hi],'all':rate(z),'churn3':rate(z[z.weak_cancel_count_3s>0]),'reprice3':rate(z[z.weak_reprice_count_3s>0])})
 out={'version':'TARGET_BTC_REPAIR_TAKER_CHURN_PARALLEL_READINESS_V32M_RESULT','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'preregistered':'TARGET_BTC_REPAIR_TAKER_CHURN_PARALLEL_READINESS_V32M_PREREGISTERED.json','marketIds':mm,'rows':len(df),'markets':len(mm),'positives':int(df.label.sum()),'split':{'trainMarkets':trm,'validationMarkets':vam,'testMarkets':tem,'trainRows':len(tr),'validationRows':len(va),'testRows':len(te),'trainPos':int(tr.label.sum()),'validationPos':int(va.label.sum()),'testPos':int(te.label.sum())},'baseline':bs,'plusChurn':cs,'lift':lift,'decision':'KEEP_CHURN_PARALLEL_READINESS_SHADOW' if keep else 'REJECT_CHURN_PARALLEL_READINESS','anatomy':anatomy,'timeChurnAnatomy':time,'boundary':['fresh later BTC chronology relative to V32L','BTC architecture only; no numeric transfer to ETH','v21 cancel candidate identity teacher-only','no winner/PnL','no threshold fitting','no 8781']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=lambda o:o.item() if hasattr(o,'item') else str(o)),encoding='utf-8'); df.to_csv(ROWS,index=False)
 print(json.dumps({k:out[k] for k in ['rows','markets','positives','split','baseline','plusChurn','lift','decision','anatomy','timeChurnAnatomy']},ensure_ascii=False,default=lambda o:o.item() if hasattr(o,'item') else str(o)))
if __name__=='__main__': main()
