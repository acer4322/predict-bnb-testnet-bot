from __future__ import annotations
import json, math
from collections import defaultdict
from pathlib import Path
import joblib, numpy as np, pandas as pd
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score
import train_target_maker_taker_coordination_big_v1 as coord
import train_post_taker_maker_reentry_hazard_v0 as tr
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'; FRESH=OUT/'forward_handoff_states_v1.csv'; REPORT=OUT/'post_taker_maker_reentry_hazard_forward_v0.json'; CSV=OUT/'post_taker_maker_reentry_hazard_forward_states_v0.csv'
def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1])) if len(y) else None}
def main():
 h=pd.read_csv(FRESH); allowed={str(x) for x in h.parent_id}; markets=set(h.market_id.astype(int)); b,t=coord.ro(coord.BOOK_DB),coord.ro(coord.TARGET_DB)
 try:
  meta=coord.load_market_meta(b); markets &= set(meta); evs=coord.load_events(t,markets); tps=coord.load_taker_parents(t,markets); mps=coord.load_anchored_maker_parents(b,markets); rows=[]; dropped=defaultdict(int)
  for m in sorted(markets,key=lambda x:int(meta[x]['window_end_ms'])):
   mend=int(meta[m]['window_end_ms']);ev=evs.get(m,[]);tp=[p for p in tps.get(m,[]) if str(p['parent_id']) in allowed];mp=mps.get(m,[]);cands=[]
   for p in tp:
    end=int(p['last_event_ms'])
    for k in range(5):
     cp=end+500+k*1000
     if cp+1000<=mend:cands.append({'cp':cp,'p':p,'end':end})
   cands.sort(key=lambda x:int(x['cp']));inv=coord.Inventory();ei=ci=0;state={'bids':{},'asks':{}};last=None
   for u in b.execute("select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id",(m,)):
    ut=int(u['source_timestamp_ms'])
    while ci<len(cands) and int(cands[ci]['cp'])<ut:
     c=cands[ci];cp=int(c['cp']);p=c['p'];end=int(c['end']);side=str(p['side']);opp='DOWN' if side=='UP' else 'UP'
     while ei<len(ev) and int(ev[ei]['event_ms'])<=cp:inv.apply(ev[ei]);ei+=1
     age=cp-last if last is not None else 10**9
     if 0<=age<=2000:
      f=inv.features(cp);cn=float(f.pop('_combined_net'));dom='UP' if cn>1e-9 else 'DOWN' if cn<-1e-9 else None;bf=coord.outcome_book(state,dom)
      if bf:
       past=[q for q in mp if end<int(q['placement_first_ms'])<=cp];ps=[q for q in past if str(q['target_side'])==side];po=[q for q in past if str(q['target_side'])==opp];ordered=sorted(past,key=lambda q:int(q['placement_first_ms']));nxt=[q for q in mp if cp<int(q['placement_first_ms'])<=cp+1000]
       def lage(xs):return float(cp-max(int(q['placement_first_ms']) for q in xs)) if xs else math.nan
       total=len(ps)+len(po);rows.append({'market_id':m,'market_end_ms':mend,'checkpoint_ms':cp,'seconds_left':(mend-cp)/1000.0,'book_age_ms':age,**f,**bf,'time_since_taker_ms':float(cp-end),'intervention_side':side,'intervention_side_is_up':float(side=='UP'),'intervention_shares':float(p['shares']),'intervention_avg_price':float(p['average_price']),'post_same_placements_so_far':float(len(ps)),'post_opp_placements_so_far':float(len(po)),'post_both_seen':float(bool(ps) and bool(po)),'post_last_place_age_ms':lage(ordered),'post_last_place_is_same':float(bool(ordered) and str(ordered[-1]['target_side'])==side),'post_last_same_place_age_ms':lage(ps),'post_last_opp_place_age_ms':lage(po),'post_place_side_balance':float((len(ps)-len(po))/total) if total else 0.0,'label_same_next1s':int(any(str(q['target_side'])==side for q in nxt)),'label_opp_next1s':int(any(str(q['target_side'])==opp for q in nxt))})
      else:dropped['empty_book']+=1
     else:dropped['book_stale']+=1
     ci+=1
    if int(u['is_checkpoint']):state={'bids':{float(k):float(v) for k,v in (coord.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (coord.dec(u['native_asks_z']) or {}).items()}}
    else:coord.apply_changes(state,coord.dec(u['changes_z']) or {})
    last=ut
  df=pd.DataFrame(rows);df.to_csv(CSV,index=False)
 finally:b.close();t.close()
 rep={'reportVersion':'POST_TAKER_MAKER_REENTRY_HAZARD_FORWARD_V0','markets':int(df.market_id.nunique()),'rows':len(df),'dropped':dict(dropped),'models':{}}
 for side in ('same','opp'):
  art=joblib.load(OUT/f'post_taker_reentry_{side}_plus_memory_v0.joblib');m=art['model'];fs=art['features'];p=m.predict_proba(coord.numeric(df,fs))[:,1];rep['models'][side]=metric(df[f'label_{side}_next1s'].astype(int),p)
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
