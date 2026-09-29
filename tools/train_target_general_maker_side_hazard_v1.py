from __future__ import annotations

import bisect
import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
DATA=OUT/'target_general_maker_side_hazard_v1.csv'
REPORT=OUT/'target_general_maker_side_hazard_v1_report.json'
UP_ART=OUT/'target_general_maker_up_hazard_v1.joblib';DOWN_ART=OUT/'target_general_maker_down_hazard_v1.joblib'
P=ROOT/'tools'/'train_target_maker_taker_coordination_big_v1.py';spec=importlib.util.spec_from_file_location('makerhaz_coord',P);coord=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=coord;spec.loader.exec_module(coord)

PLACE=['last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms','placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s','up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak']
CORE=list(coord.CORE);BOOK=list(coord.BOOK);LIFE=list(coord.LIFE);ECON=list(coord.ECON)
SETS={'CORE':CORE,'CORE_BOOK':CORE+BOOK,'CORE_BOOK_FILL_LIFE':CORE+BOOK+LIFE,'FULL_WITH_PLACE_LIFE':CORE+BOOK+LIFE+ECON+PLACE}

def placement_features(parents:list[dict[str,Any]],times:list[int],cp:int)->dict[str,float]:
 i=bisect.bisect_right(times,cp)-1
 if i<0:return {k:(math.nan if 'age' in k else 0.0) for k in PLACE}
 last=parents[i];lu=ld=None;streak=0;last_side=str(last['target_side'])
 for j in range(i,-1,-1):
  p=parents[j];side=str(p['target_side']);t=int(p['placement_first_ms'])
  if side=='UP' and lu is None:lu=t
  if side=='DOWN' and ld is None:ld=t
  if side==last_side:streak+=1
  elif j<i:break
 def counts(w):
  lo=bisect.bisect_right(times,cp-w);xs=parents[lo:i+1];u=sum(str(p['target_side'])=='UP' for p in xs);d=len(xs)-u;return len(xs),u,d
 n1,u1,d1=counts(1000);n5,u5,d5=counts(5000);n10,u10,d10=counts(10000)
 def bal(u,d):return (u-d)/(u+d) if u+d else 0.0
 return {'last_place_age_ms':float(cp-int(last['placement_first_ms'])),'last_up_place_age_ms':float(cp-lu) if lu is not None else math.nan,'last_down_place_age_ms':float(cp-ld) if ld is not None else math.nan,'placements_1s':float(n1),'placements_5s':float(n5),'placements_10s':float(n10),'up_placements_5s':float(u5),'down_placements_5s':float(d5),'up_placements_10s':float(u10),'down_placements_10s':float(d10),'placement_side_balance_5s':bal(u5,d5),'placement_side_balance_10s':bal(u10,d10),'placement_side_streak':float(streak)}
def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def numeric(d,fs):return d.reindex(columns=fs).apply(pd.to_numeric,errors='coerce')
def model(seed):return HistGradientBoostingClassifier(max_iter=350,learning_rate=.06,max_leaf_nodes=31,l2_regularization=1.0,early_stopping=True,validation_fraction=.12,n_iter_no_change=30,random_state=seed)

def build():
 b,t=coord.ro(coord.BOOK_DB),coord.ro(coord.TARGET_DB)
 try:
  meta=coord.load_market_meta(b);update_markets={int(r[0]) for r in b.execute('select distinct market_id from maker_book_inference_updates')};target_markets={int(r[0]) for r in t.execute("select distinct market_id from wallet_shadow_target_events where asset='BTC' and quote_type='BID'")};markets=set(meta)&update_markets&target_markets;events=coord.load_events(t,markets);parents=coord.load_anchored_maker_parents(b,markets);rows=[];drop=defaultdict(int)
  for mi,m in enumerate(sorted(markets,key=lambda x:int(meta[x]['window_end_ms'])),1):
   if not events.get(m):continue
   mend=int(meta[m]['window_end_ms']);mstart=mend-300000;ev=events[m];ps=parents.get(m,[]);pt=[int(p['placement_first_ms']) for p in ps];inv=coord.Inventory();ei=0;state={'bids':{},'asks':{}};last=None;checkpoints=list(range(mstart+500,mend-1500,1000));ci=0
   for u in b.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(m,)):
    ut=int(u['source_timestamp_ms'])
    while ci<len(checkpoints) and checkpoints[ci]<ut:
     cp=checkpoints[ci]
     while ei<len(ev) and int(ev[ei]['event_ms'])<=cp:inv.apply(ev[ei]);ei+=1
     age=cp-last if last is not None else 10**9
     if 0<=age<=2000:
      f=inv.features(cp);cn=float(f.pop('_combined_net'));dom='UP' if cn>1e-9 else 'DOWN' if cn<-1e-9 else None;bf=coord.outcome_book(state,dom)
      if bf:
       j0=bisect.bisect_right(pt,cp);j1=bisect.bisect_right(pt,cp+1000);future=ps[j0:j1];rows.append({'market_id':m,'market_end_ms':mend,'checkpoint_ms':cp,'book_age_ms':age,'seconds_left':(mend-cp)/1000.0,'label_up_next1s':int(any(str(p['target_side'])=='UP' for p in future)),'label_down_next1s':int(any(str(p['target_side'])=='DOWN' for p in future)),**f,**bf,**placement_features(ps,pt,cp)})
      else:drop['empty_book']+=1
     else:drop['book_stale']+=1
     ci+=1
    if int(u['is_checkpoint']):state={'bids':{float(k):float(v) for k,v in (coord.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (coord.dec(u['native_asks_z']) or {}).items()}}
    else:coord.apply_changes(state,coord.dec(u['changes_z']) or {})
    last=ut
   if mi%75==0:print(json.dumps({'progressMarkets':mi,'total':len(markets),'rows':len(rows)}),flush=True)
  d=pd.DataFrame(rows).sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True);d.to_csv(DATA,index=False);return d,dict(drop)
 finally:b.close();t.close()
def train(d):
 sp=coord.split_markets(d);reports={};arts={}
 for setname,fs in SETS.items():
  reports[setname]={}
  for side,label,seed in [('UP','label_up_next1s',20260830),('DOWN','label_down_next1s',20260831)]:
   m=model(seed);tr=d.market_id.astype(int).isin(sp['train']);m.fit(numeric(d.loc[tr],fs),d.loc[tr,label].astype(int));key=f'{setname}_{side}';reports[setname][side]={'features':fs}
   for k,ms in sp.items():q=d.market_id.astype(int).isin(ms);reports[setname][side][k]=metric(d.loc[q,label],m.predict_proba(numeric(d.loc[q],fs))[:,1])
   if setname=='FULL_WITH_PLACE_LIFE':path=UP_ART if side=='UP' else DOWN_ART;joblib.dump({'version':'TARGET_GENERAL_MAKER_SIDE_HAZARD_V1','side':side,'features':fs,'model':m},path);arts[side]=str(path)
 return sp,reports,arts
def main():
 d,drop=build();sp,reports,arts=train(d);rep={'reportVersion':'TARGET_GENERAL_MAKER_SIDE_HAZARD_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'question':'Can all-market Target passive Maker activity be represented as separate next-1s UP/DOWN placement hazards using strict-past portfolio, book and lifecycle state?','coverage':{'rows':len(d),'markets':int(d.market_id.nunique()),'dropped':drop,'upPositiveRate':float(d.label_up_next1s.mean()),'downPositiveRate':float(d.label_down_next1s.mean())},'splitMarkets':{k:len(v) for k,v in sp.items()},'featureSets':reports,'artifacts':arts,'labelBoundary':'High-confidence 8778 anchored placement_first_ms in (checkpoint,checkpoint+1s] is teacher label. Retrospective ownership is label/private-state reconstruction only; inference may use only public book + equivalent own portfolio/fill/placement history.','guards':['No threshold tuning.','Same HGB capacity for all feature sets and both sides.','Chronological market split.','Placement lifecycle features use only placements at or before checkpoint; future placement enters label only.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
