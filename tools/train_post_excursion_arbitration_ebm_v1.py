from __future__ import annotations

import bisect
import importlib.util
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
TARGET_DB = ROOT/'data'/'target_wallet_official_v1.db'
BOOK_DB = ROOT/'data'/'wallet_maker_book_inference.db'
PUB_DB = ROOT/'data'/'strategy_target_compare_v1.db'
EXCURSIONS = OUT/'target_temporary_imbalance_recovery_v0_rows.csv'
DATASET = OUT/'post_excursion_arbitration_states_v1.csv'
PASSIVE_ART = OUT/'post_excursion_passive_repair_1s_ebm_v1.joblib'
TAKER_ART = OUT/'post_excursion_taker_escalation_1s_ebm_v1.joblib'
REPORT = OUT/'post_excursion_arbitration_ebm_v1_report.json'
CONTRACT = OUT/'post_excursion_arbitration_ebm_v1_contract.json'
BASE='UNIFIED_CONTROLLER_PAPER_V1%'
GRID=.01
EPS=1e-9
SEED=20260820

# Import the already-audited coordination state builder so portfolio/book semantics stay identical.
P=ROOT/'tools'/'train_target_maker_taker_coordination_big_v1.py'
spec=importlib.util.spec_from_file_location('coord_post_exc_v1',P)
coord=importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name]=coord; spec.loader.exec_module(coord)

CORE=[
 'seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage',
 'taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage',
 'combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage',
 'worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign',
]
BOOK=[
 'up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth',
 'down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth',
 'pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge',
]
LIFE=[
 'last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms',
 'last_taker_up_age_ms','last_taker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s',
 'taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s',
 'taker_shares_5s','taker_shares_10s','maker_side_streak','taker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s',
]
ECON=['maker_avg_pair_edge','taker_avg_pair_edge','combined_avg_pair_edge']
EPISODE=[
 'excursion_side_is_up','excursion_age_ms','excursion_pre_maker_abs_net','excursion_start_maker_abs_net',
 'excursion_expansion_shares','excursion_pre_maker_paired_coverage','active_same_count','active_opp_count',
 'active_same_age_ms','active_opp_age_ms','active_same_offset_ticks','active_opp_offset_ticks',
 'direction_alignment','direction_score','volatility_level',
]
PRESSURE=['maker_pressure_same','maker_pressure_opp','taker_pressure_1s','maker_pressure_gap']
FEATURES=CORE+BOOK+LIFE+ECON+EPISODE+PRESSURE


def ro(p:Path):
 c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=30); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def num(v):
 try:
  x=float(v); return x if math.isfinite(x) else math.nan
 except Exception:return math.nan

def load_public(c):
 out=defaultdict(list)
 for r in c.execute("select market_id,coalesce(source_snapshot_ms,decision_ms) ms,public_state_json from our_decisions where strategy_version like ? and public_state_json is not null order by market_id,ms",(BASE,)):
  try:s=json.loads(str(r['public_state_json']))
  except Exception:continue
  if isinstance(s,dict):out[int(r['market_id'])].append((int(r['ms']),s))
 return out

def public_before(xs,ms,max_age=2000):
 if not xs:return None
 ts=[x[0] for x in xs]; i=bisect.bisect_right(ts,ms)-1
 if i<0:return None
 t,s=xs[i]; return s if 0<=ms-t<=max_age else None

def load_events(c,markets):
 out=defaultdict(list); ids=sorted(markets)
 for st in range(0,len(ids),250):
  b=ids[st:st+250]; qs=','.join('?'*len(b))
  sql=f"select market_id,event_ms,role,side,price,shares,id from wallet_shadow_target_events where market_id in ({qs}) and asset='BTC' and quote_type='BID' and role in ('MAKER','TAKER') and side in ('UP','DOWN') order by market_id,event_ms,id"
  for r in c.execute(sql,b):out[int(r['market_id'])].append(dict(r))
 return out

def load_taker_starts(c,markets):
 out=defaultdict(list); ids=sorted(markets)
 for st in range(0,len(ids),250):
  b=ids[st:st+250]; qs=','.join('?'*len(b))
  sql=f"select market_id,first_event_ms,parent_id,side from target_parent_orders where market_id in ({qs}) and asset='BTC' and role='TAKER' and quote_type='BID' and first_event_ms is not null order by market_id,first_event_ms,parent_id"
  for r in c.execute(sql,b):out[int(r['market_id'])].append(dict(r))
 return out

def load_parents(c,markets):
 out=defaultdict(list); ids=sorted(markets)
 for st in range(0,len(ids),250):
  b=ids[st:st+250]; qs=','.join('?'*len(b))
  sql=f'''select parent_id,market_id,target_side,target_price,placement_first_ms,last_target_ms
          from maker_book_inference_v21_parent_lifecycles where market_id in ({qs}) and placement_supports_18=1
          and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75
          and placement_first_ms is not null and last_target_ms is not null order by market_id,placement_first_ms,parent_id'''
  for r in c.execute(sql,b):out[int(r['market_id'])].append(dict(r))
 return out

def inv_at(events,ms):
 mu=md=0.0
 for e in events:
  if int(e['event_ms'])>ms:break
  if e['role']!='MAKER':continue
  if e['side']=='UP':mu+=float(e['shares'])
  else:md+=float(e['shares'])
 return mu-md

def first_recovered_ms(events,anchor,pre_abs,end):
 net=inv_at(events,anchor)
 for e in events:
  t=int(e['event_ms'])
  if t<=anchor:continue
  if t>end:break
  if e['role']=='MAKER':
   sh=float(e['shares']); net += sh if e['side']=='UP' else -sh
   if abs(net)<=pre_abs+1.0:return t
 return None

def next_passive_repair(events,cp,end):
 net=inv_at(events,cp)
 for e in events:
  t=int(e['event_ms'])
  if t<=cp:continue
  if t>end:break
  if e['role']!='MAKER':continue
  sh=float(e['shares']); after=net+(sh if e['side']=='UP' else -sh)
  if abs(after)<abs(net)-EPS:return 1
  net=after
 return 0

def active_geom(parents,cp,side,bf):
 opp='DOWN' if side=='UP' else 'UP'
 same=[p for p in parents if p['target_side']==side and int(p['placement_first_ms'])<=cp<int(p['last_target_ms'])]
 other=[p for p in parents if p['target_side']==opp and int(p['placement_first_ms'])<=cp<int(p['last_target_ms'])]
 def one(xs,s):
  if not xs:return (0,math.nan,math.nan)
  p=max(xs,key=lambda z:(int(z['placement_first_ms']),str(z['parent_id']))); age=cp-int(p['placement_first_ms'])
  bid=bf['up_bid'] if s=='UP' else bf['down_bid']; off=(float(bid)-float(p['target_price']))/GRID
  return (len(xs),float(age),float(off))
 a,aa,ao=one(same,side); b,ba,bo=one(other,opp)
 return a,b,aa,ba,ao,bo

def vol_level(s):
 v=str(s.get('volatilityAlert') or s.get('volatility_alert') or 'UNKNOWN').upper()
 return {'NORMAL':0.0,'WATCH':1.0,'HIGH':2.0}.get(v,-1.0)
def align_num(s,side):
 b=str(s.get('directionBias') or s.get('direction_bias') or 'NEUTRAL').upper()
 if b not in ('UP','DOWN'):return 0.0
 return 1.0 if b==side else -1.0

def fast_numeric(df,features):return df[features].apply(pd.to_numeric,errors='coerce')
def score_model(art,df):return art['model'].predict_proba(fast_numeric(df,list(art['features'])))[:,1]

def build_dataset():
 ex=pd.read_csv(EXCURSIONS)
 markets=set(map(int,ex.marketId.astype(int).unique().tolist()))
 tc=ro(TARGET_DB); bc=ro(BOOK_DB); pc=ro(PUB_DB)
 try:
  events=load_events(tc,markets); takers=load_taker_starts(tc,markets); parents=load_parents(bc,markets); public=load_public(pc)
  meta={int(r['market_id']):int(r['window_end_ms']) for r in bc.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null')}
  # Build runtime-like episode checkpoints. Keep only latest unresolved excursion per market/cp/side.
  episode_by_market=defaultdict(dict)
  raw_candidates=0
  for _,r in ex.iterrows():
   m=int(r.marketId); anchor=int(r.anchorFillEndMs); mend=meta.get(m,0)
   if not mend:continue
   ts=[int(x['first_event_ms']) for x in takers.get(m,[]) if int(x['first_event_ms'])>anchor]
   first_t=min(ts) if ts else None
   end=min(anchor+15000,mend-1)
   rec=first_recovered_ms(events.get(m,[]),anchor,float(r.makerPreAbsNet),end)
   cutoff=min([x for x in (end,first_t,rec) if x is not None])
   for k in range(15):
    cp=anchor+1+k*1000
    if cp>=cutoff:break
    raw_candidates+=1; key=(cp,str(r.side))
    old=episode_by_market[m].get(key)
    if old is None or int(old['anchorFillEndMs'])<anchor: episode_by_market[m][key]=r.to_dict()
  rows=[]; dropped=defaultdict(int)
  for mi,m in enumerate(sorted(episode_by_market,key=lambda x:meta.get(x,0)),1):
   cands=sorted((cp,side,ep) for (cp,side),ep in episode_by_market[m].items())
   if not cands:continue
   ev=events.get(m,[]); mp=parents.get(m,[]); pbs=public.get(m,[]); mend=meta[m]
   ups=list(bc.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(m,)))
   state={'bids':{},'asks':{}}; last=None; ui=0
   for cp,side,ep in cands:
    while ui<len(ups) and int(ups[ui]['source_timestamp_ms'])<=cp:
     u=ups[ui]
     if int(u['is_checkpoint']):
      state={'bids':{float(k):float(v) for k,v in (coord.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (coord.dec(u['native_asks_z']) or {}).items()}}
     else:coord.apply_changes(state,coord.dec(u['changes_z']) or {})
     last=int(u['source_timestamp_ms']); ui+=1
    # Reconstruct exact strict-past Target portfolio at each checkpoint. This is intentionally independent
    # per row: it avoids state-pointer errors when many overlapping excursion episodes interleave.
    inv=coord.Inventory()
    for ee in ev:
     if int(ee['event_ms'])>cp: break
     inv.apply({'event_ms':int(ee['event_ms']),'role':str(ee['role']),'side':str(ee['side']),'price':float(ee['price']),'shares':float(ee['shares'])})
    if last is None or not (0<=cp-last<=2000):dropped['stale_book']+=1; continue
    ps=public_before(pbs,cp,2000)
    if ps is None:dropped['stale_public']+=1; continue
    feat=inv.features(cp); cn=float(feat.pop('_combined_net')); dom='UP' if cn>EPS else 'DOWN' if cn<-EPS else None
    bf=coord.outcome_book(state,dom)
    if bf is None:dropped['empty_book']+=1; continue
    ac,oc,aa,oa,aoff,ooff=active_geom(mp,cp,side,bf)
    # First Taker parent after this episode anchor. Candidates cease once it begins.
    anchor=int(ep['anchorFillEndMs']); after=[int(x['first_event_ms']) for x in takers.get(m,[]) if int(x['first_event_ms'])>anchor]
    ft=min(after) if after else None
    horizon=min(cp+1000,mend-1,ft if ft is not None else cp+1000)
    y_t=int(ft is not None and cp<ft<=cp+1000)
    y_m=next_passive_repair(ev,cp,horizon)
    row={'market_id':m,'market_end_ms':mend,'checkpoint_ms':cp,'book_age_ms':cp-last,
         'episode_parent_id':str(ep['parentId']),'excursion_side':side,
         'label_passive_repair_1s':y_m,'label_taker_escalation_1s':y_t,
         'seconds_left':(mend-cp)/1000.0,**feat,**bf,
         'excursion_side_is_up':float(side=='UP'),'excursion_age_ms':float(cp-anchor),
         'excursion_pre_maker_abs_net':float(ep['makerPreAbsNet']),'excursion_start_maker_abs_net':float(ep['makerPostAbsNet']),
         'excursion_expansion_shares':float(ep['makerExpansion']),'excursion_pre_maker_paired_coverage':float(ep['makerPairedCoveragePre']),
         'active_same_count':float(ac),'active_opp_count':float(oc),'active_same_age_ms':aa,'active_opp_age_ms':oa,
         'active_same_offset_ticks':aoff,'active_opp_offset_ticks':ooff,
         'direction_alignment':align_num(ps,side),'direction_score':num(ps.get('directionScore') if 'directionScore' in ps else ps.get('direction_score')),
         'volatility_level':vol_level(ps)}
    rows.append(row)
   if mi%50==0:print(json.dumps({'progressMarkets':mi,'rows':len(rows)}),flush=True)
  df=pd.DataFrame(rows).sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True)
  # Frozen mature pressure modules become deployable meta-features; no Target future values are inputs.
  up=joblib.load(OUT/'target_general_maker_up_core_book_v1.joblib'); dn=joblib.load(OUT/'target_general_maker_down_core_book_v1.joblib'); th=joblib.load(OUT/'frozen_hazard_1s_full.joblib')
  df['maker_pressure_up']=score_model(up,df); df['maker_pressure_down']=score_model(dn,df); df['taker_pressure_1s']=score_model(th,df)
  df['maker_pressure_same']=np.where(df.excursion_side.astype(str).eq('UP'),df.maker_pressure_up,df.maker_pressure_down)
  df['maker_pressure_opp']=np.where(df.excursion_side.astype(str).eq('UP'),df.maker_pressure_down,df.maker_pressure_up)
  df['maker_pressure_gap']=df.maker_pressure_opp-df.maker_pressure_same
  DATASET.parent.mkdir(parents=True,exist_ok=True); df.to_csv(DATASET,index=False)
  return df,{'rawEpisodeCheckpoints':raw_candidates,'dedupedStates':len(df),'markets':int(df.market_id.nunique()),'dropped':dict(dropped)}
 finally:tc.close();bc.close();pc.close()

def split_markets(df):
 ms=df[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']); ids=ms.market_id.astype(int).tolist(); n=len(ids)
 a=int(n*.65); b=int(n*.80); c=int(n*.90)
 return {'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:c]),'lateHoldout':set(ids[c:])}

def ebm(features,seed):
 return ExplainableBoostingClassifier(feature_names=features,max_bins=96,max_interaction_bins=32,interactions=8,outer_bags=4,learning_rate=.035,max_rounds=1800,early_stopping_rounds=80,min_samples_leaf=12,n_jobs=-2,random_state=seed)
def metrics(y,p):
 y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-7,1-1e-7); both=len(set(y.tolist()))>1
 order=np.argsort(-p); k=max(1,int(math.ceil(len(y)*.10))); top=order[:k]
 return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,
         'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum() else None,
         'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None,'brier':float(brier_score_loss(y,p)) if len(y) else None,
         'top10pctPositiveRate':float(y[top].mean()) if len(y) else None,'top10pctRecall':float(y[top].sum()/y.sum()) if y.sum() else None}
def top_terms(m,n=20):
 imp=list(m.term_importances()); names=list(m.term_names_); ix=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:n]
 return [{'term':str(names[i]),'importance':float(imp[i])} for i in ix]
def train_task(df,splits,label,task,art_path,seed):
 parts={k:df[df.market_id.astype(int).isin(v)].copy() for k,v in splits.items()}
 m=ebm(FEATURES,seed); m.fit(fast_numeric(parts['train'],FEATURES),parts['train'][label].astype(int))
 joblib.dump({'version':'POST_EXCURSION_ARBITRATION_EBM_V1','task':task,'label':label,'features':FEATURES,'model':m,'trainingMarkets':sorted(splits['train'])},art_path)
 out={'task':task,'label':label,'features':FEATURES,'splitMetrics':{},'topTerms':top_terms(m),'artifact':str(art_path)}
 for k,d in parts.items():out['splitMetrics'][k]=metrics(d[label].astype(int),m.predict_proba(fast_numeric(d,FEATURES))[:,1])
 return out

def main():
 df,cov=build_dataset(); splits=split_markets(df)
 print(json.dumps({'dataset':cov,'positiveRates':{'passive':float(df.label_passive_repair_1s.mean()),'taker':float(df.label_taker_escalation_1s.mean())},'splitMarkets':{k:len(v) for k,v in splits.items()}},indent=2),flush=True)
 passive=train_task(df,splits,'label_passive_repair_1s','PASSIVE_MAKER_REPAIR_1S',PASSIVE_ART,SEED+1)
 print(json.dumps({'passive':passive['splitMetrics'],'topTerms':passive['topTerms'][:10]},indent=2),flush=True)
 taker=train_task(df,splits,'label_taker_escalation_1s','TAKER_ESCALATION_1S',TAKER_ART,SEED+2)
 print(json.dumps({'taker':taker['splitMetrics'],'topTerms':taker['topTerms'][:10]},indent=2),flush=True)
 rep={'reportVersion':'POST_EXCURSION_ARBITRATION_EBM_V1','researchOnly':True,'liveTradingChanges':False,'runtimeTargetDataAllowed':False,
      'goal':'Distill Target sequential controller behavior after a realized temporary Maker inventory excursion: learn passive opposite-Maker repair pressure and active Taker escalation as separate 1s hazards.',
      'dataset':cov,'datasetPath':str(DATASET),'splitMarkets':{k:len(v) for k,v in splits.items()},
      'chronology':{'trainMaxEndMs':int(df[df.market_id.astype(int).isin(splits['train'])].market_end_ms.max()),'validationMaxEndMs':int(df[df.market_id.astype(int).isin(splits['validation'])].market_end_ms.max()),'testMaxEndMs':int(df[df.market_id.astype(int).isin(splits['test'])].market_end_ms.max()),'lateHoldoutMinEndMs':int(df[df.market_id.astype(int).isin(splits['lateHoldout'])].market_end_ms.min())},
      'labels':{'passiveRepair':'next 1s contains a Target Maker fill that reduces instantaneous Maker |net|, before the first post-excursion Taker begins','takerEscalation':'first Target Taker parent after excursion begins in next 1s; episode checkpoints stop after that first Taker or after Maker inventory fully recovers to pre-excursion level or 15s'},
      'features':FEATURES,'passiveRepair':passive,'takerEscalation':taker,
      'guards':['Target future behavior is label/episode endpoint only; no winner or future action is a feature.','Inputs are public book + quantities OUR can reconstruct from its own portfolio/fills/order state; Target inferred active-parent state is teacher reconstruction of runtime-known own order state.','Frozen mature Maker/Taker hazards are used only as deployable pressure meta-features.','No threshold sweep, no profit tuning, no same-cohort hyperparameter search.','LateHoldout is reported once as an untouched chronological research holdout; it is not prospective after this report.']}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
 contract={'version':'POST_EXCURSION_ARBITRATION_EBM_V1','frozenAtReport':str(REPORT),'trainingMaxMarketEndMs':rep['chronology']['trainMaxEndMs'],'features':FEATURES,'artifacts':{'passiveRepair':str(PASSIVE_ART),'takerEscalation':str(TAKER_ART)},'runtimeTargetDataAllowed':False,'next':'Evaluate same Target state -> target-blind own-state closed-loop; do not retrain from forward outcomes before reporting.'}
 CONTRACT.write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'report':str(REPORT),'contract':str(CONTRACT)},indent=2))
if __name__=='__main__':main()
