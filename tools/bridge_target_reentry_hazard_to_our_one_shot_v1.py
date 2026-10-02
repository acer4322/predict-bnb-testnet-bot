from __future__ import annotations

import importlib.util,json,math,sys
from pathlib import Path
from typing import Any
import joblib,numpy as np,pandas as pd
from sklearn.metrics import average_precision_score,roc_auc_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'bridge_target_reentry_hazard_to_our_synthetic_repair_add_v0.py';spec=importlib.util.spec_from_file_location('oneshot_syn',P);syn=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=syn;spec.loader.exec_module(syn);br=syn.br;base=br.base;mod=br.mod;coord=br.coord
PN=ROOT/'tools'/'train_scale_normalized_effect_conditioned_reentry_v1.py';spec2=importlib.util.spec_from_file_location('oneshot_norm',PN);norm=importlib.util.module_from_spec(spec2);assert spec2 and spec2.loader;sys.modules[spec2.name]=norm;spec2.loader.exec_module(norm)
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1';REPORT=OUT/'our_one_shot_reentry_hazard_bridge_v1_report.json';DATA=OUT/'our_one_shot_reentry_hazard_bridge_v1_states.csv';SAME=OUT/'post_taker_reentry_same_plus_memory_v0.joblib';OPP=OUT/'post_taker_reentry_opp_plus_memory_v0.joblib';OPPN=OUT/'post_taker_reentry_opp_scale_norm_effect_v1.joblib';SHARES=18.0;EPS=1e-9;HORIZON=20000;MIN_HORIZON=16000
ACTIONS=('SAME_ONE_SHOT','OPP_ONE_SHOT','NO_NEW_ORDER')

def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None}
def direct_add(sim,snapshot,ns,now,side):
 active={o.side:o for o in sim.orders.values()}
 if side in active:return 0
 tick=mod.maker_ebm._quote_tick(snapshot,side,1)
 if tick is None:return 0
 tick=int(tick);price=round(tick*mod.maker_ebm.GRID,2);opp='DOWN' if side=='UP' else 'UP';oo=active.get(opp)
 if oo is not None:
  while price+float(oo.price)>mod.maker_ebm.MAX_PAIR_PRICE_SUM+1e-9:
   tick-=1
   if tick<int(round(mod.maker_ebm.MIN_PRICE/mod.maker_ebm.GRID)):return 0
   price=round(tick*mod.maker_ebm.GRID,2)
 key=(side,tick)
 if now-int(sim.last_closed.get(key,0))<mod.maker_ebm.REFILL_COOLDOWN_MS:return 0
 sim.orders[key]=mod.SimOrder(key=key,side=side,price_tick=tick,price=price,shares=mod.maker_ebm.SHARES_PER_ORDER,placed_at_ms=now,placed_snapshot_ns=ns);sim.placements+=1;return 1

def one_shot_branch(src,action,items,j,market_id,seeds,seed_idx,tside):
 sim=br.cf.clone_sim(src);start=items[j];start_ms=int(start['decision_ms']);ss=dict(start['snapshot']);smtm=br.cf.mtm(sim,ss)
 if smtm is None:return None
 spair=mod.fifo_pair([dict(x) for x in sim.maker_fills]);sfloor=br.cf.floor_value(sim);sinv=br.cf.inventory_state(sim);sfills=len(sim.maker_fills);local=seed_idx;placed=0;end=ss;endms=start_ms
 ns=int(mod.num(ss.get('timestampNs')) or mod.num(ss.get('timestamp_ns')) or start_ms*1_000_000)
 if action!='NO_NEW_ORDER':placed=direct_add(sim,ss,ns,start_ms,tside if action=='SAME_ONE_SHOT' else ('DOWN' if tside=='UP' else 'UP'))
 for k in range(j+1,len(items)):
  it=items[k];now=int(it['decision_ms'])
  if now-start_ms>HORIZON:break
  snap=dict(it['snapshot']);ns2=int(mod.num(snap.get('timestampNs')) or mod.num(snap.get('timestamp_ns')) or now*1_000_000);filled=sim.fill_existing(snap,ns2,now);local,seedfill=br.cf.apply_due_seeds(sim,seeds,local,now);dec=sim.decide(snap,market_id,now);sim.apply_plan(dec,ns2,now,allow_new=(filled==0 and not seedfill));end=snap;endms=now
 if endms-start_ms<MIN_HORIZON:return None
 emtm=br.cf.mtm(sim,end);epair=mod.fifo_pair([dict(x) for x in sim.maker_fills]);einv=br.cf.inventory_state(sim)
 return {'placementAdded':placed,'mtmDelta':float(emtm-smtm),'pairEdgeDelta':float(epair['lockedEdgeUsdt']-spair['lockedEdgeUsdt']),'pairedSharesDelta':float(epair['pairedShares']-spair['pairedShares']),'absNetDelta':float(einv['absNet']-sinv['absNet']),'floorDelta':float(br.cf.floor_value(sim)-sfloor),'makerFills':int(len(sim.maker_fills)-sfills),'horizonMs':int(endms-start_ms)}

def norm_score(art,raw,effect):
 d=pd.DataFrame([raw]);d['label_effect']=effect;X=norm.transform(d);return float(art['model'].predict_proba(X)[0,1])
def eval_rows(rr,score):
 if not rr:return {'n':0}
 y=[int(r['oracleMtmBest']=='OPP_ONE_SHOT') for r in rr];p=[r[score] for r in rr];delta=[r['oppMinusSameMtm'] for r in rr]
 return {'n':len(rr),'oppActionable':sum(int(r['oppPlacementAdded']) for r in rr),'meanScore':float(np.mean(p)),'pOppVsOppMtmBest':metric(y,p),'spearmanScoreVsOppMinusSameMtm':float(pd.Series(p).corr(pd.Series(delta),method='spearman')),'oracleMtmBestCounts':pd.Series([r['oracleMtmBest'] for r in rr]).value_counts().to_dict(),'meanOppMinusSameMtm':float(np.mean(delta))}

def main():
 samea=joblib.load(SAME);oppa=joblib.load(OPP);oppn=joblib.load(OPPN);our=mod.ro(mod.DEFAULT_OUR_DB);book=br.ro(br.BOOK_DB)
 try:
  snaps=mod.load_snapshots(our);seedmap=mod.load_seeds(our);models=mod.maker_ebm.load_models();emap=br.first_book_market_by_end(book);rows=[];drop={}
  for m in sorted(set(snaps)&set(seedmap)):
   items=snaps[m];seeds=list(seedmap[m]);ref=base.DirectionalSim(models);si=0;sampled=False
   for i,it in enumerate(items):
    now=int(it['decision_ms']);snap=dict(it['snapshot']);ns=int(mod.num(snap.get('timestampNs')) or mod.num(snap.get('timestamp_ns')) or now*1_000_000);before=len(ref.maker_fills);filled=ref.fill_existing(snap,ns,now);new=ref.maker_fills[before:];dec=ref.decide(snap,m,now);sf=False
    while si<len(seeds) and int(seeds[si]['filled_at_ms'])<=now:ref.apply_seed(seeds[si]);si+=1;sf=True
    sec=mod.snapshot_value(snap,'seconds_left','secondsLeft')
    if (not sampled) and si>0 and new and sec is not None and float(sec)>=35:
     _,_,cn0,dom0=br.own_inventory(ref,now)
     if dom0 and abs(cn0)>=SHARES-EPS:
      j=syn.next_idx(items,i,now+500)
      if j is not None:
       cp=int(items[j]['decision_ms']);ws=int(mod.snapshot_value(dict(items[j]['snapshot']),'window_end_ms','windowEndMs') or 0);bm=emap.get(ws)
       if bm is not None:
        state,last=br.book_state_before(book,bm,cp);age=cp-last if last is not None else 10**9
        if 0<=age<=2000:
         for effect in ('REPAIR_EFFECT','ADD_EFFECT'):
          tside=('DOWN' if dom0=='UP' else 'UP') if effect=='REPAIR_EFFECT' else dom0;px=syn.ask_for(snap,tside)
          if px is None:continue
          sim=br.cf.clone_sim(ref);sim.apply_seed({'side':tside,'price':float(px),'shares':SHARES,'filled_at_ms':now})
          for k in range(i+1,j+1):s2=dict(items[k]['snapshot']);n2=int(items[k]['decision_ms']);ns2=int(mod.num(s2.get('timestampNs')) or mod.num(s2.get('timestamp_ns')) or n2*1_000_000);sim.fill_existing(s2,ns2,n2)
          _,feat,cn,dom=br.own_inventory(sim,cp);bf=coord.outcome_book(state,dom)
          if bf is None:continue
          raw={'seconds_left':(ws-cp)/1000.0,**feat,**bf,'time_since_taker_ms':float(cp-now),'intervention_side_is_up':float(tside=='UP'),'intervention_shares':SHARES,'intervention_avg_price':float(px),**syn.mem_zero()}
          ps=br.score(samea,raw);po=br.score(oppa,raw);pon=norm_score(oppn,raw,effect);branches={a:one_shot_branch(sim,a,items,j,m,seeds,si,tside) for a in ACTIONS}
          if not all(branches[a] is not None for a in ACTIONS):continue
          best=max(ACTIONS,key=lambda a:branches[a]['mtmDelta']);rows.append({'marketId':m,'windowEndMs':ws,'effect':effect,'checkpointMs':cp,'pSame':ps,'pOpp':po,'pOppNormV1':pon,'oracleMtmBest':best,'oppPlacementAdded':branches['OPP_ONE_SHOT']['placementAdded'],'samePlacementAdded':branches['SAME_ONE_SHOT']['placementAdded'],'oppMinusSameMtm':branches['OPP_ONE_SHOT']['mtmDelta']-branches['SAME_ONE_SHOT']['mtmDelta'],'oppMinusNoMtm':branches['OPP_ONE_SHOT']['mtmDelta']-branches['NO_NEW_ORDER']['mtmDelta'],'oppMinusSamePairEdge':branches['OPP_ONE_SHOT']['pairEdgeDelta']-branches['SAME_ONE_SHOT']['pairEdgeDelta'],'oppMinusSamePairedShares':branches['OPP_ONE_SHOT']['pairedSharesDelta']-branches['SAME_ONE_SHOT']['pairedSharesDelta'],'oppMinusSameFloor':branches['OPP_ONE_SHOT']['floorDelta']-branches['SAME_ONE_SHOT']['floorDelta'],'oppAbsNetImprovement':branches['SAME_ONE_SHOT']['absNetDelta']-branches['OPP_ONE_SHOT']['absNetDelta']})
         sampled=True
    ref.apply_plan(dec,ns,now,allow_new=(filled==0 and not sf))
  rows.sort(key=lambda r:(r['windowEndMs'],r['marketId'],r['effect']));pd.DataFrame(rows).to_csv(DATA,index=False)
  rep={'reportVersion':'OUR_ONE_SHOT_REENTRY_HAZARD_BRIDGE_V1','researchOnly':True,'question':'When action semantics are aligned to the Target next-1s placement label, do frozen Target OPP hazards rank OUR one-shot OPP placement value after synthetic REPAIR/ADD?','coverage':{'rows':len(rows),'markets':len({r['marketId'] for r in rows})},'results':{e:{'original':eval_rows([r for r in rows if r['effect']==e],'pOpp'),'scaleNorm':eval_rows([r for r in rows if r['effect']==e],'pOppNormV1')} for e in ('REPAIR_EFFECT','ADD_EFFECT')},'actionSemantics':'At +0.5s checkpoint, optionally add one -1tick SAME or OPP Maker order without cancelling existing resting orders; resume baseline controller from next recorder snapshot. NO_NEW_ORDER preserves existing orders.','guards':['No threshold or hyperparameter tuning.','Synthetic fixed 18-share intervention remains a V0 probe.','All OUR markets are after Target hazard training cutoff.','Target future behavior/winner never enters OUR counterfactual outcomes.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
 finally:our.close();book.close()
if __name__=='__main__':raise SystemExit(main())
