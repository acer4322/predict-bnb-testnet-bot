from __future__ import annotations

import importlib.util, json, math, sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'bridge_target_reentry_hazard_to_our_open_counterfactual_v0.py'
spec=importlib.util.spec_from_file_location('syn_bridge_base',P);br=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=br;spec.loader.exec_module(br)
base=br.base;mod=br.mod;coord=br.coord

OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
REPORT=OUT/'our_synthetic_repair_add_reentry_hazard_bridge_v0_report.json'
DATA=OUT/'our_synthetic_repair_add_reentry_hazard_bridge_v0_states.csv'
SAME_ART=OUT/'post_taker_reentry_same_plus_memory_v0.joblib';OPP_ART=OUT/'post_taker_reentry_opp_plus_memory_v0.joblib'
SHARES=18.0; MIN_SEC=35.0; EPS=1e-9
ACTIONS=br.ACTIONS

def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None}

def ask_for(snapshot,side):
 return mod.snapshot_value(snapshot,'predict_up_ask','predictUpAsk') if side=='UP' else mod.snapshot_value(snapshot,'predict_down_ask','predictDownAsk')

def next_idx(items,i,min_ms):
 for j in range(i+1,len(items)):
  if int(items[j]['decision_ms'])>=min_ms:return j
 return None

def mem_zero():
 return {'post_same_placements_so_far':0.0,'post_opp_placements_so_far':0.0,'post_both_seen':0.0,'post_last_place_age_ms':math.nan,'post_last_place_is_same':0.0,'post_last_same_place_age_ms':math.nan,'post_last_opp_place_age_ms':math.nan,'post_place_side_balance':0.0}

def eval_group(rr):
 if not rr:return {'n':0}
 sw=[int(r['oracleMtmBest']=='SWITCH_OPPOSITE') for r in rr];sa=[int(r['oracleMtmBest']=='CONTINUE_SAME') for r in rr];po=[r['pOpp'] for r in rr];ps=[r['pSame'] for r in rr]
 dss=[r['switchMinusSameMtm'] for r in rr];dsp=[r['switchMinusPauseMtm'] for r in rr]
 return {'n':len(rr),'markets':len({r['marketId'] for r in rr}),'meanPOpp':float(np.mean(po)),'meanPSame':float(np.mean(ps)),'pOppVsSwitchMtmBest':metric(sw,po),'pSameVsContinueMtmBest':metric(sa,ps),'spearmanPOppVsSwitchMinusSameMtm':float(pd.Series(po).corr(pd.Series(dss),method='spearman')),'spearmanPOppVsSwitchMinusPauseMtm':float(pd.Series(po).corr(pd.Series(dsp),method='spearman')),'oracleMtmBestCounts':pd.Series([r['oracleMtmBest'] for r in rr]).value_counts().to_dict(),'meanSwitchMinusSameMtm':float(np.mean(dss)),'meanSwitchMinusPauseMtm':float(np.mean(dsp))}

def main():
 same_art=joblib.load(SAME_ART);opp_art=joblib.load(OPP_ART)
 our=mod.ro(mod.DEFAULT_OUR_DB);book=br.ro(br.BOOK_DB)
 try:
  snapshots=mod.load_snapshots(our);seeds_by_market=mod.load_seeds(our);models=mod.maker_ebm.load_models();end_to_book=br.first_book_market_by_end(book)
  rows=[];drop={}
  for market_id in sorted(set(snapshots)&set(seeds_by_market)):
   items=snapshots[market_id];seeds=list(seeds_by_market.get(market_id,[]));ref=base.DirectionalSim(models);seed_idx=0;sampled=False
   for i,item in enumerate(items):
    now=int(item['decision_ms']);snap=dict(item['snapshot']);ns=int(mod.num(snap.get('timestampNs')) or mod.num(snap.get('timestamp_ns')) or now*1_000_000)
    before=len(ref.maker_fills);filled=ref.fill_existing(snap,ns,now);newfills=ref.maker_fills[before:];decision=ref.decide(snap,market_id,now);seed_filled=False
    while seed_idx<len(seeds) and int(seeds[seed_idx]['filled_at_ms'])<=now:ref.apply_seed(seeds[seed_idx]);seed_idx+=1;seed_filled=True
    sec=mod.snapshot_value(snap,'seconds_left','secondsLeft')
    if (not sampled) and seed_idx>0 and newfills and sec is not None and float(sec)>=MIN_SEC:
     _,f0,cn0,dom0=br.own_inventory(ref,now)
     if dom0 and abs(cn0)>=SHARES-EPS:
      j=next_idx(items,i,now+500)
      if j is not None:
       cp=int(items[j]['decision_ms']);window_end=int(mod.snapshot_value(dict(items[j]['snapshot']),'window_end_ms','windowEndMs') or 0);bm=end_to_book.get(window_end)
       if bm is None:drop['book_market_missing']=drop.get('book_market_missing',0)+1
       else:
        state,last=br.book_state_before(book,bm,cp);age=cp-last if last is not None else 10**9
        if not 0<=age<=2000:drop['book_stale']=drop.get('book_stale',0)+1
        else:
         for effect in ('REPAIR_EFFECT','ADD_EFFECT'):
          tside=('DOWN' if dom0=='UP' else 'UP') if effect=='REPAIR_EFFECT' else dom0;px=ask_for(snap,tside)
          if px is None:continue
          syn=br.cf.clone_sim(ref);fake={'side':tside,'price':float(px),'shares':SHARES,'filled_at_ms':now};syn.apply_seed(fake)
          # Advance only execution of already-resting Maker orders to the +0.5s checkpoint; no new Maker plan before scoring.
          for k in range(i+1,j+1):
           s2=dict(items[k]['snapshot']);n2=int(items[k]['decision_ms']);ns2=int(mod.num(s2.get('timestampNs')) or mod.num(s2.get('timestamp_ns')) or n2*1_000_000);syn.fill_existing(s2,ns2,n2)
          _,feat,cn,dom=br.own_inventory(syn,cp);bf=coord.outcome_book(state,dom)
          if bf is None:continue
          model_row={'seconds_left':(window_end-cp)/1000.0,**feat,**bf,'time_since_taker_ms':float(cp-now),'intervention_side_is_up':float(tside=='UP'),'intervention_shares':SHARES,'intervention_avg_price':float(px),**mem_zero()}
          ps=br.score(same_art,model_row);po=br.score(opp_art,model_row)
          branches={a:br.branch_run(syn,a,items,j,market_id,seeds,seed_idx,tside) for a in ACTIONS}
          if not all(branches[a] is not None for a in ACTIONS):drop['short_horizon']=drop.get('short_horizon',0)+1;continue
          best=max(ACTIONS,key=lambda a:float(branches[a]['mtmDelta']))
          row={'marketId':market_id,'windowEndMs':window_end,'syntheticEffect':effect,'sourceCombinedNetBefore':cn0,'takerSide':tside,'takerPrice':float(px),'checkpointMs':cp,'bookAgeMs':age,'pSame':ps,'pOpp':po,'postCombinedNet':cn,'postCombinedPairedCoverage':model_row.get('combined_paired_coverage'),'oracleMtmBest':best,'switchMinusSameMtm':float(branches['SWITCH_OPPOSITE']['mtmDelta']-branches['CONTINUE_SAME']['mtmDelta']),'switchMinusPauseMtm':float(branches['SWITCH_OPPOSITE']['mtmDelta']-branches['PAUSE']['mtmDelta']),'sameMtm':branches['CONTINUE_SAME']['mtmDelta'],'switchMtm':branches['SWITCH_OPPOSITE']['mtmDelta'],'pauseMtm':branches['PAUSE']['mtmDelta'],'sameFloor':branches['CONTINUE_SAME']['floorDelta'],'switchFloor':branches['SWITCH_OPPOSITE']['floorDelta'],'pauseFloor':branches['PAUSE']['floorDelta'],'samePairEdge':branches['CONTINUE_SAME']['pairEdgeDelta'],'switchPairEdge':branches['SWITCH_OPPOSITE']['pairEdgeDelta'],'pausePairEdge':branches['PAUSE']['pairEdgeDelta'],'samePairedShares':branches['CONTINUE_SAME']['pairedSharesDelta'],'switchPairedShares':branches['SWITCH_OPPOSITE']['pairedSharesDelta'],'pausePairedShares':branches['PAUSE']['pairedSharesDelta'],'sameAbsNetDelta':branches['CONTINUE_SAME']['absNetDelta'],'switchAbsNetDelta':branches['SWITCH_OPPOSITE']['absNetDelta'],'pauseAbsNetDelta':branches['PAUSE']['absNetDelta']}; row.update({f'state_{k}':v for k,v in model_row.items()}); rows.append(row)
         sampled=True
    ref.apply_plan(decision,ns,now,allow_new=(filled==0 and not seed_filled))
  rows.sort(key=lambda r:(r['windowEndMs'],r['marketId'],r['syntheticEffect']));pd.DataFrame(rows).to_csv(DATA,index=False)
  rep={'reportVersion':'TARGET_REENTRY_HAZARD_TO_OUR_SYNTHETIC_REPAIR_ADD_V0','researchOnly':True,'liveTradingChanges':False,'question':'When OUR is placed into semantically aligned synthetic 18-share REPAIR/ADD post-Taker states, do frozen Target re-entry hazards rank OUR SAME/OPP/PAUSE counterfactual value?','coverage':{'rows':len(rows),'markets':len({r['marketId'] for r in rows}),'dropped':drop},'fixedSyntheticIntervention':{'shares':SHARES,'price':'current public ask','REPAIR':'buy current combined minority side','ADD':'buy current combined dominant side','noSweep':True},'hazardTrainingMaxMarketEndMs':1787041800000,'minBridgeMarketEndMs':min((r['windowEndMs'] for r in rows),default=None),'allAfterTraining':bool(rows and min(r['windowEndMs'] for r in rows)>1787041800000),'results':{e:eval_group([r for r in rows if r['syntheticEffect']==e]) for e in ('REPAIR_EFFECT','ADD_EFFECT')},'guard':['Synthetic Taker intervention is a causal paper probe, not a deployed OUR Taker policy.','No threshold/model/size sweep.','Target future behavior/winner is never used in OUR outcome construction.','If the bridge works only for REPAIR/ADD and not OPEN, intervention-effect-conditioned lifecycle is supported.']}
  REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
 finally:our.close();book.close()
if __name__=='__main__':raise SystemExit(main())
