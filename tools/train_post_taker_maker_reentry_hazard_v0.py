from __future__ import annotations

import bisect, json, math
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score

import train_target_maker_taker_coordination_big_v1 as coord

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
HAND=OUT/'post_taker_handoff_states_v1.csv'
DATA=OUT/'post_taker_maker_reentry_hazard_v0.csv'
REPORT=OUT/'post_taker_maker_reentry_hazard_v0_report.json'

CTX=['time_since_taker_ms','intervention_side_is_up','intervention_shares','intervention_avg_price']
MEM=['post_same_placements_so_far','post_opp_placements_so_far','post_both_seen','post_last_place_age_ms','post_last_place_is_same','post_last_same_place_age_ms','post_last_opp_place_age_ms','post_place_side_balance']
BASE=coord.FEATURE_SETS['FULL']+CTX
PLUS=BASE+MEM

def finite(v):
 try:x=float(v); return x if math.isfinite(x) else math.nan
 except:return math.nan

def metric(y,p):
 y=np.asarray(y,int); p=np.asarray(p,float); both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1])) if len(y) else None}

def train(df,features,label,name):
 sp=coord.split_markets(df); parts={k:df[df.market_id.astype(int).isin(v)].copy() for k,v in sp.items()}; m=HistGradientBoostingClassifier(max_iter=350,learning_rate=.06,max_leaf_nodes=31,l2_regularization=1.0,early_stopping=True,validation_fraction=.12,n_iter_no_change=30,random_state=20260819+(1 if 'same' in label else 2)); m.fit(coord.numeric(parts['train'],features),parts['train'][label].astype(int)); rep={'features':features,'splitMarkets':{k:len(v) for k,v in sp.items()}}
 for k in ('train','validation','test'):
  p=m.predict_proba(coord.numeric(parts[k],features))[:,1]; rep[k]=metric(parts[k][label].astype(int),p)
 art=OUT/f'post_taker_reentry_{name}.joblib'; joblib.dump({'features':features,'model':m,'label':label},art); rep['artifact']=str(art); return rep

def main():
 h=pd.read_csv(HAND); allowed={str(x) for x in h.parent_id}; markets=set(h.market_id.astype(int))
 b,t=coord.ro(coord.BOOK_DB),coord.ro(coord.TARGET_DB)
 try:
  meta=coord.load_market_meta(b); markets=markets & set(meta); evs=coord.load_events(t,markets); tps=coord.load_taker_parents(t,markets); mps=coord.load_anchored_maker_parents(b,markets)
  rows=[]; dropped=defaultdict(int)
  for mi,m in enumerate(sorted(markets,key=lambda x:int(meta[x]['window_end_ms'])),1):
   mend=int(meta[m]['window_end_ms']); ev=evs.get(m,[]); tp=[p for p in tps.get(m,[]) if str(p['parent_id']) in allowed]; mp=mps.get(m,[])
   candidates=[]
   for p in tp:
    end=int(p['last_event_ms'])
    for k in range(5):
     cp=end+500+k*1000
     if cp+1000<=mend: candidates.append({'cp':cp,'p':p,'end':end})
   candidates.sort(key=lambda x:int(x['cp']))
   inv=coord.Inventory(); ei=0;ci=0;state={'bids':{},'asks':{}};last=None
   for u in b.execute("select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id",(m,)):
    ut=int(u['source_timestamp_ms'])
    while ci<len(candidates) and int(candidates[ci]['cp'])<ut:
     c=candidates[ci];cp=int(c['cp']);p=c['p'];end=int(c['end']);side=str(p['side']);opp='DOWN' if side=='UP' else 'UP'
     while ei<len(ev) and int(ev[ei]['event_ms'])<=cp: inv.apply(ev[ei]);ei+=1
     age=cp-last if last is not None else 10**9
     if 0<=age<=2000:
      f=inv.features(cp);cn=float(f.pop('_combined_net'));dom='UP' if cn>1e-9 else 'DOWN' if cn<-1e-9 else None;bf=coord.outcome_book(state,dom)
      if bf:
       past=[q for q in mp if end<int(q['placement_first_ms'])<=cp]
       ps=[q for q in past if str(q['target_side'])==side];po=[q for q in past if str(q['target_side'])==opp];ordered=sorted(past,key=lambda q:int(q['placement_first_ms']))
       nxt=[q for q in mp if cp<int(q['placement_first_ms'])<=cp+1000]
       same=int(any(str(q['target_side'])==side for q in nxt));oppl=int(any(str(q['target_side'])==opp for q in nxt))
       def lage(xs): return float(cp-max(int(q['placement_first_ms']) for q in xs)) if xs else math.nan
       total=len(ps)+len(po)
       row={'market_id':m,'market_end_ms':mend,'checkpoint_ms':cp,'seconds_left':(mend-cp)/1000.0,'book_age_ms':age,**f,**bf,'time_since_taker_ms':float(cp-end),'intervention_side':side,'intervention_side_is_up':float(side=='UP'),'intervention_shares':float(p['shares']),'intervention_avg_price':float(p['average_price']),'post_same_placements_so_far':float(len(ps)),'post_opp_placements_so_far':float(len(po)),'post_both_seen':float(bool(ps) and bool(po)),'post_last_place_age_ms':lage(ordered),'post_last_place_is_same':float(bool(ordered) and str(ordered[-1]['target_side'])==side),'post_last_same_place_age_ms':lage(ps),'post_last_opp_place_age_ms':lage(po),'post_place_side_balance':float((len(ps)-len(po))/total) if total else 0.0,'label_same_next1s':same,'label_opp_next1s':oppl}
       rows.append(row)
      else:dropped['empty_book']+=1
     else:dropped['book_stale']+=1
     ci+=1
    if int(u['is_checkpoint']): state={'bids':{float(k):float(v) for k,v in (coord.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (coord.dec(u['native_asks_z']) or {}).items()}}
    else: coord.apply_changes(state,coord.dec(u['changes_z']) or {})
    last=ut
   if mi%50==0: print(json.dumps({'progressMarkets':mi,'total':len(markets),'rows':len(rows)}),flush=True)
  df=pd.DataFrame(rows); df.to_csv(DATA,index=False)
 finally:b.close();t.close()
 reps={}
 for label,short in [('label_same_next1s','same'),('label_opp_next1s','opp')]:
  reps[f'{short}_base']=train(df,BASE,label,f'{short}_base_v0'); reps[f'{short}_plus_memory']=train(df,PLUS,label,f'{short}_plus_memory_v0')
 report={'reportVersion':'POST_TAKER_MAKER_REENTRY_HAZARD_V0','researchOnly':True,'runtimeTargetDataAllowed':False,'question':'Can post-Taker Maker handoff be modeled as sequential side-specific 1s placement hazards instead of a one-shot 5s four-class decision?','coverage':{'rows':len(df),'markets':int(df.market_id.nunique()),'takerParents':len(allowed),'dropped':dict(dropped),'samePositiveRate':float(df.label_same_next1s.mean()),'oppPositiveRate':float(df.label_opp_next1s.mean()),'bothPositiveRate':float(((df.label_same_next1s==1)&(df.label_opp_next1s==1)).mean())},'featureSets':{'BASE':BASE,'PLUS_POST_PLACEMENT_MEMORY':PLUS},'models':reps,'comparison':{}}
 for side in ('same','opp'):
  report['comparison'][side]={}
  for split in ('validation','test'):
   a=reps[f'{side}_base'][split]; z=reps[f'{side}_plus_memory'][split]; report['comparison'][side][split]={'baseAuc':a['rocAuc'],'memoryAuc':z['rocAuc'],'aucLift':(z['rocAuc']-a['rocAuc']) if a['rocAuc'] is not None and z['rocAuc'] is not None else None,'baseAP':a['averagePrecision'],'memoryAP':z['averagePrecision'],'apLift':(z['averagePrecision']-a['averagePrecision']) if a['averagePrecision'] is not None and z['averagePrecision'] is not None else None}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
