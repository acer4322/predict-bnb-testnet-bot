from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
B=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=B/'TARGET_BTC_REPAIR_TAKER_PASSIVE_PIPELINE_ESCALATION_TEACHER_V32L_RESULT.json'
HAZ=ROOT/'data/research/target_taker_action_burst_hazard_v1.csv'
MKR=ROOT/'data/wallet_maker_book_inference.db'
OFF=ROOT/'data/target_wallet_official_v1.db'
BASE=[
'seconds_left','spot_minus_strike_bps','abs_spot_minus_strike_bps','predict_up_mid','predict_down_mid','direction_score','abs_direction_score','predict_bias_edge_abs',
'spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps','spot_realized_vol_10s_bps','predict_up_mid_slope_5s_per_s','signal_age_ms',
'pre_up_shares','pre_down_shares','pre_abs_net','pre_cost','pre_floor','pre_best_pnl','weak_side_up','weak_bid','weak_ask','weak_mid']
PIPE=['pending_weak_count','pending_weak_oldest_age_s','pending_weak_youngest_age_s','pending_weak_min_behind_ticks','pending_weak_max_placement_coverage',
'weak_placement_starts_3s','weak_placement_starts_10s','weak_cancel_count_3s','weak_cancel_count_10s','weak_reprice_count_10s','weak_maker_fill_count_3s','weak_maker_fill_count_10s','weak_maker_fill_shares_10s','seconds_since_last_weak_maker_fill','seconds_since_last_weak_placement','pending_without_recent_payment',
'open_weak_cancel_candidate_count','open_weak_cancel_candidate_oldest_age_s','open_weak_cancel_candidate_min_behind_ticks','weak_candidate_starts_3s','weak_candidate_starts_10s']

def safe(v):
 try:
  x=float(v); return x if math.isfinite(x) else np.nan
 except Exception:return np.nan

def fit_score(train,val,test,features):
 m=HistGradientBoostingClassifier(max_leaf_nodes=15,learning_rate=.05,max_iter=160,l2_regularization=1.0,random_state=32)
 m.fit(train[features].to_numpy(float),train['label_repair_taker_1s'].to_numpy(int))
 out={}
 for name,z in [('validation',val),('test',test)]:
  y=z['label_repair_taker_1s'].to_numpy(int); p=m.predict_proba(z[features].to_numpy(float))[:,1]
  out[name]={'rows':len(z),'positives':int(y.sum()),'rocAuc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'brier':float(brier_score_loss(y,p))}
 return m,out

def main():
 usecols=list(dict.fromkeys(['market_id','decision_sampled_at_ms','decision_bucket_start_ms']+BASE[:17]))
 h=pd.read_csv(HAZ,usecols=usecols)
 h=h[(h.market_id>=1396133)&(h.market_id<=1404405)].copy()
 cm=sqlite3.connect(f'file:{MKR.resolve().as_posix()}?mode=ro',uri=True); cm.row_factory=sqlite3.Row
 co=sqlite3.connect(f'file:{OFF.resolve().as_posix()}?mode=ro',uri=True); co.row_factory=sqlite3.Row
 vm={int(r[0]) for r in cm.execute('select market_id from maker_book_inference_v21_market_meta where market_id between 1396133 and 1404405')}
 om={int(r[0]) for r in co.execute('select market_id from target_markets where market_id between 1396133 and 1404405')}
 mids=sorted(set(h.market_id.astype(int).unique()) & vm & om)
 h=h[h.market_id.isin(mids)].sort_values(['market_id','decision_bucket_start_ms'])
 rows=[]
 for mid,g in h.groupby('market_id',sort=True):
  mid=int(mid)
  ev=[dict(r) for r in co.execute("select event_ms,role,side,price,shares from wallet_shadow_target_events where market_id=? and asset='BTC' order by event_ms,id",(mid,))]
  makerfills={'UP':[],'DOWN':[]}; taker_by_bucket=defaultdict(list)
  for e in ev:
   if e['role']=='MAKER': makerfills[e['side']].append((int(e['event_ms']),float(e['shares'])))
   elif e['role']=='TAKER': taker_by_bucket[(int(e['event_ms'])//1000)*1000].append(e['side'])
  life=[dict(r) for r in cm.execute('select target_side,target_price,first_target_ms,placement_first_ms,placement_last_ms,placement_coverage from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_first_ms is not null order by placement_first_ms',(mid,))]
  canc=[dict(r) for r in cm.execute('select target_side,target_price,placement_source_ms,cancel_source_ms,resting_ms,post_action from maker_book_inference_v21_cancel_candidates where market_id=? order by placement_source_ms',(mid,))]
  i=0; up=dn=cost=0.0
  for rr in g.itertuples(index=False):
   tb=int(rr.decision_bucket_start_ms); ts=int(rr.decision_sampled_at_ms)
   while i<len(ev) and int(ev[i]['event_ms'])<tb:
    e=ev[i]; q=float(e['shares']); p=float(e['price']); cost+=p*q
    if e['side']=='UP':up+=q
    elif e['side']=='DOWN':dn+=q
    i+=1
   if abs(up-dn)<=1e-9: continue
   floor=min(up,dn)-cost
   if floor>=-1e-9: continue
   weak='UP' if up<dn else 'DOWN'; weak_up=1.0 if weak=='UP' else 0.0
   weak_bid=safe(getattr(rr,'predict_up_mid') if False else (getattr(rr,'predict_up_mid') if weak=='UP' else getattr(rr,'predict_down_mid')))
   # true bid/ask aren't in BASE usecols above; reconstruct from mids conservatively as mid +/- unavailable -> use mid as price proxy only for behind teacher
   weak_mid=safe(getattr(rr,'predict_up_mid') if weak=='UP' else getattr(rr,'predict_down_mid'))
   # reload bid/ask lazily from source values absent in current tuple is not possible; use mid for behind diagnostic and expose weak_bid=weak_mid, weak_ask=weak_mid
   weak_bid=weak_mid; weak_ask=weak_mid
   pend=[x for x in life if x['target_side']==weak and int(x['placement_first_ms'])<ts and int(x['first_target_ms'])>=ts]
   ages=[(ts-int(x['placement_first_ms']))/1000.0 for x in pend]
   behind=[max(0.0,(weak_bid-float(x['target_price']))/.01) for x in pend if math.isfinite(weak_bid)]
   pstarts=[int(x['placement_first_ms']) for x in life if x['target_side']==weak and int(x['placement_first_ms'])<ts]
   cc=[x for x in canc if x['target_side']==weak]
   opencc=[x for x in cc if int(x['placement_source_ms'])<ts<int(x['cancel_source_ms'])]
   ccages=[(ts-int(x['placement_source_ms']))/1000.0 for x in opencc]
   ccbehind=[max(0.0,(weak_bid-float(x['target_price']))/.01) for x in opencc if math.isfinite(weak_bid)]
   fills=[x for x in makerfills[weak] if x[0]<tb]
   def cnt_time(arr,w,idx=0):return sum(1 for x in arr if (ts-w)<=int(x[idx] if isinstance(x,tuple) else x)<ts)
   f3=[x for x in fills if tb-3000<=x[0]<tb]; f10=[x for x in fills if tb-10000<=x[0]<tb]
   cancel3=[x for x in cc if ts-3000<=int(x['cancel_source_ms'])<ts]; cancel10=[x for x in cc if ts-10000<=int(x['cancel_source_ms'])<ts]
   starts3=[x for x in pstarts if ts-3000<=x<ts]; starts10=[x for x in pstarts if ts-10000<=x<ts]
   cstarts3=[x for x in cc if ts-3000<=int(x['placement_source_ms'])<ts]; cstarts10=[x for x in cc if ts-10000<=int(x['placement_source_ms'])<ts]
   r={k:safe(getattr(rr,k)) for k in BASE[:17]}
   r.update({'market_id':mid,'decision_bucket_start_ms':tb,'pre_up_shares':up,'pre_down_shares':dn,'pre_abs_net':abs(up-dn),'pre_cost':cost,'pre_floor':floor,'pre_best_pnl':max(up,dn)-cost,'weak_side_up':weak_up,'weak_bid':weak_bid,'weak_ask':weak_ask,'weak_mid':weak_mid,
   'pending_weak_count':len(pend),'pending_weak_oldest_age_s':max(ages) if ages else np.nan,'pending_weak_youngest_age_s':min(ages) if ages else np.nan,'pending_weak_min_behind_ticks':min(behind) if behind else np.nan,'pending_weak_max_placement_coverage':max([safe(x['placement_coverage']) for x in pend],default=np.nan),
   'weak_placement_starts_3s':len(starts3),'weak_placement_starts_10s':len(starts10),'weak_cancel_count_3s':len(cancel3),'weak_cancel_count_10s':len(cancel10),'weak_reprice_count_10s':sum(str(x.get('post_action') or '').startswith('REPRICE') for x in cancel10),
   'weak_maker_fill_count_3s':len(f3),'weak_maker_fill_count_10s':len(f10),'weak_maker_fill_shares_10s':sum(x[1] for x in f10),'seconds_since_last_weak_maker_fill':(tb-fills[-1][0])/1000.0 if fills else np.nan,'seconds_since_last_weak_placement':(ts-pstarts[-1])/1000.0 if pstarts else np.nan,'pending_without_recent_payment':1.0 if pend and not f3 else 0.0,
   'open_weak_cancel_candidate_count':len(opencc),'open_weak_cancel_candidate_oldest_age_s':max(ccages) if ccages else np.nan,'open_weak_cancel_candidate_min_behind_ticks':min(ccbehind) if ccbehind else np.nan,'weak_candidate_starts_3s':len(cstarts3),'weak_candidate_starts_10s':len(cstarts10),
   'label_repair_taker_1s':1 if weak in taker_by_bucket.get(tb+1000,[]) else 0})
   rows.append(r)
 cm.close(); co.close()
 df=pd.DataFrame(rows)
 markets=sorted(df.market_id.unique()); trainm=markets[:26]; valm=markets[26:34]; testm=markets[34:42]
 tr=df[df.market_id.isin(trainm)].copy(); va=df[df.market_id.isin(valm)].copy(); te=df[df.market_id.isin(testm)].copy()
 _,bscore=fit_score(tr,va,te,BASE); _,pscore=fit_score(tr,va,te,BASE+PIPE)
 lift={'testRocAuc':pscore['test']['rocAuc']-bscore['test']['rocAuc'],'testAveragePrecision':pscore['test']['averagePrecision']-bscore['test']['averagePrecision'],'testBrierImprovement':bscore['test']['brier']-pscore['test']['brier']}
 keep=lift['testRocAuc']>=.02 and lift['testAveragePrecision']>=.01
 def rate(z):return {'rows':len(z),'positives':int(z.label_repair_taker_1s.sum()),'rate':float(z.label_repair_taker_1s.mean()) if len(z) else None}
 anatomy={
  'pendingWeak':rate(df[df.pending_weak_count>0]),'noPendingWeak':rate(df[df.pending_weak_count==0]),
  'pendingNoRecentPayment':rate(df[df.pending_without_recent_payment>0]),
  'pendingWithRecentPayment':rate(df[(df.pending_weak_count>0)&(df.weak_maker_fill_count_3s>0)]),
  'openCancelCandidate':rate(df[df.open_weak_cancel_candidate_count>0]),'noOpenCancelCandidate':rate(df[df.open_weak_cancel_candidate_count==0])}
 # time bins x pending for descriptive anatomy
 tbins=[]
 for lo,hi in [(240,301),(180,240),(120,180),(60,120),(30,60),(0,30)]:
  z=df[(df.seconds_left>=lo)&(df.seconds_left<hi)]
  tbins.append({'secondsLeft':[lo,hi],'all':rate(z),'pending':rate(z[z.pending_weak_count>0]),'pendingNoRecentPayment':rate(z[z.pending_without_recent_payment>0])})
 out={'version':'TARGET_BTC_REPAIR_TAKER_PASSIVE_PIPELINE_ESCALATION_TEACHER_V32L_RESULT','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'preregistered':'TARGET_BTC_REPAIR_TAKER_PASSIVE_PIPELINE_ESCALATION_TEACHER_V32L_PREREGISTERED.json','rows':len(df),'markets':len(markets),'positives':int(df.label_repair_taker_1s.sum()),'split':{'trainMarkets':trainm,'validationMarkets':valm,'testMarkets':testm,'trainRows':len(tr),'validationRows':len(va),'testRows':len(te),'trainPos':int(tr.label_repair_taker_1s.sum()),'validationPos':int(va.label_repair_taker_1s.sum()),'testPos':int(te.label_repair_taker_1s.sum())},'baseline':bscore,'plusPipeline':pscore,'lift':lift,'decision':'KEEP_PIPELINE_ESCALATION_TEACHER' if keep else 'REJECT_PIPELINE_ESCALATION_TEACHER','anatomy':anatomy,'timePendingAnatomy':tbins,'featureGroups':{'base':BASE,'pipeline':PIPE},'boundary':['BTC architecture teacher only; no numeric transfer to ETH','v21 Target placement identity is retrospective teacher-only','official granular fills strict before decision bucket for inventory','no winner/PnL','no threshold fitting','no 8781']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=lambda o:o.item() if hasattr(o,'item') else str(o)),encoding='utf-8')
 df.to_csv(B/'TARGET_BTC_REPAIR_TAKER_PASSIVE_PIPELINE_ESCALATION_TEACHER_V32L_ROWS.csv',index=False)
 print(json.dumps({k:out[k] for k in ['rows','markets','positives','split','baseline','plusPipeline','lift','decision','anatomy','timePendingAnatomy']},ensure_ascii=False,default=lambda o:o.item() if hasattr(o,'item') else str(o)))
if __name__=='__main__':main()
