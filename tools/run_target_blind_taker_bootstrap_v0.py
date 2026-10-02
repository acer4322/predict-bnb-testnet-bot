from __future__ import annotations

import bisect
import importlib.util
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'bridge_target_reentry_hazard_to_our_open_counterfactual_v0.py'
spec=importlib.util.spec_from_file_location('bootstrap_base',P);br=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=br;spec.loader.exec_module(br)
base=br.base;mod=br.mod;coord=br.coord
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1';CONTRACT=OUT/'forward_contract_v1.json';REPORT=OUT/'target_blind_taker_bootstrap_v0_report.json';MARKETS=OUT/'target_blind_taker_bootstrap_v0_markets.csv';EVENTS=OUT/'target_blind_taker_bootstrap_v0_generated_takers.csv';STATES=OUT/'target_blind_taker_bootstrap_v0_states.csv';TEVAL=OUT/'target_blind_taker_bootstrap_v0_target_event_eval.csv';BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db';TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db';HIST_T=OUT/'taker_event_states_v1.csv';FRESH_T=OUT/'forward_taker_states_v1.csv'
SHARES=18.0;RNG_SEED=20260819

def ro(path):
 c=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro',uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c
def numeric_row(raw,fs):return np.asarray([[float(raw.get(f,math.nan)) if raw.get(f) is not None else math.nan for f in fs]],dtype=float)

def fast_hgb_probability(model, features):
 kb,fm=model._bin_mapper.make_known_categories_bitsets();trees=[it[0] for it in model._predictors];base_raw=float(model._baseline_prediction[0,0])
 def predict(raw):
  x=np.asarray([[float(raw.get(f,math.nan)) if raw.get(f) is not None else math.nan for f in features]],dtype=float);z=base_raw
  for tree in trees:z+=float(tree.predict(x,known_cat_bitsets=kb,f_idx_map=fm,n_threads=1)[0])
  if z>=0:return 1.0/(1.0+math.exp(-z))
  ez=math.exp(z);return ez/(1.0+ez)
 return predict

def teacher_events():
 ps=[]
 for p in (HIST_T,FRESH_T):
  if p.exists():ps.append(pd.read_csv(p))
 return pd.concat(ps,ignore_index=True,sort=False).drop_duplicates('parent_id',keep='last') if ps else pd.DataFrame()
def endmap(book):
 d={}
 for r in book.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null order by market_id'):d.setdefault(int(r['window_end_ms']),int(r['market_id']))
 return d
def effect_label(pre_net,side,shares):return coord.effect_label(float(pre_net),str(side),float(shares))
def multi(y,p):
 if not y:return {'n':0}
 return {'n':len(y),'accuracy':float(accuracy_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,p)),'macroF1':float(f1_score(y,p,average='macro',zero_division=0)),'truthDistribution':pd.Series(y).value_counts().to_dict(),'predictedDistribution':pd.Series(p).value_counts().to_dict()}
def ask(bf,side):return bf.get('up_ask') if side=='UP' else bf.get('down_ask')

def main():
 contract=json.loads(CONTRACT.read_text(encoding='utf-8'));haz=joblib.load(contract['artifacts']['hazard_1s']);side_art=joblib.load(contract['artifacts']['side']);effect_art=joblib.load(contract['artifacts']['effect']);hfs=list(haz['features']);sfs=list(side_art['features']);efs=list(effect_art['features'])
 fast_hazard=fast_hgb_probability(haz['model'],hfs)
 our=mod.ro(mod.DEFAULT_OUR_DB);book=ro(BOOK_DB);target=ro(TARGET_DB)
 try:
  snaps=mod.load_snapshots(our);seedmap=mod.load_seeds(our);models=mod.maker_ebm.load_models();emap=endmap(book);rng=np.random.default_rng(RNG_SEED);rows=[];gen=[];market_rows=[];drop={}
  target_count={int(r['market_id']):int(r['n']) for r in target.execute("select market_id,count(*) n from target_parent_orders where asset='BTC' and role='TAKER' and quote_type='BID' group by market_id")}
  for mi,om in enumerate(sorted(snaps),1):
   items=snaps[om]
   if not items:continue
   ws=int(mod.snapshot_value(dict(items[0]['snapshot']),'window_end_ms','windowEndMs') or 0);tm=emap.get(ws)
   if tm is None:continue
   ups=list(book.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(tm,)));ui=0;state={'bids':{},'asks':{}};last=None;ref=base.DirectionalSim(models);inv=coord.Inventory();seeds=list(seedmap.get(om,[]));si=0;generated=0;pvals=[]
   for it in items:
    now=int(it['decision_ms']);snap=dict(it['snapshot'])
    while ui<len(ups) and int(ups[ui]['source_timestamp_ms'])<=now:
     u=ups[ui]
     if int(u['is_checkpoint']):state={'bids':{float(k):float(v) for k,v in (coord.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (coord.dec(u['native_asks_z']) or {}).items()}}
     else:coord.apply_changes(state,coord.dec(u['changes_z']) or {})
     last=int(u['source_timestamp_ms']);ui+=1
    ns=int(mod.num(snap.get('timestampNs')) or mod.num(snap.get('timestamp_ns')) or now*1_000_000);before_maker=len(ref.maker_fills);maker_filled=ref.fill_existing(snap,ns,now);active_taker=False
    for mf in ref.maker_fills[before_maker:]: inv.apply({'event_ms':int(mf['at_ms']),'role':'MAKER','side':str(mf['side']),'price':float(mf['price']),'shares':float(mf['shares'])})
    while si<len(seeds) and int(seeds[si]['filled_at_ms'])<=now:
     sd=seeds[si];ref.apply_seed(sd);inv.apply({'event_ms':int(sd['filled_at_ms']),'role':'TAKER','side':str(sd['side']),'price':float(sd['price']),'shares':float(sd['shares'])});si+=1;active_taker=True
    age=now-last if last is not None else 10**9
    if 0<=age<=2000:
     feat=inv.features(now);cn=float(feat.pop('_combined_net'));dom='UP' if cn>1e-9 else 'DOWN' if cn<-1e-9 else None;bf=coord.outcome_book(state,dom)
     if bf is not None:
      raw={'seconds_left':(ws-now)/1000.0,**feat,**bf};p=float(fast_hazard(raw));pvals.append(p)
      triggered=bool(rng.random()<p)
      generated_side=None;generated_effect=None;generated_price=None
      if triggered:
       probs=side_art['model'].predict_proba(numeric_row(raw,sfs))[0];classes=list(side_art['model'].classes_);generated_side=str(rng.choice(classes,p=np.asarray(probs,float)/np.sum(probs)));generated_price=ask(bf,generated_side)
       if generated_price is not None and math.isfinite(float(generated_price)):
        generated_effect=effect_label(cn,generated_side,SHARES);gd={'side':generated_side,'price':float(generated_price),'shares':SHARES,'filled_at_ms':now};ref.apply_seed(gd);inv.apply({'event_ms':now,'role':'TAKER','side':generated_side,'price':float(generated_price),'shares':SHARES});generated+=1;active_taker=True;gen.append({'ourMarketId':om,'targetMarketId':tm,'windowEndMs':ws,'eventMs':now,'side':generated_side,'shares':SHARES,'price':float(generated_price),'realizedEffect':generated_effect,'pTaker1s':p})
      rows.append({'our_market_id':om,'target_market_id':tm,'window_end_ms':ws,'decision_ms':now,'p_taker_1s':p,'generated_taker':int(triggered and generated_price is not None),**raw})
     else:drop['empty_book']=drop.get('empty_book',0)+1
    else:drop['book_stale']=drop.get('book_stale',0)+1
    dec=ref.decide(snap,om,now);ref.apply_plan(dec,ns,now,allow_new=(maker_filled==0 and not active_taker))
   market_rows.append({'ourMarketId':om,'targetMarketId':tm,'windowEndMs':ws,'originalOpenSeeds':len(seeds),'generatedTakers':generated,'studentTakersTotal':len(seeds)+generated,'targetTakerParents':target_count.get(tm,0),'meanHazardP1':float(np.mean(pvals)) if pvals else None,'sumHazardP1':float(np.sum(pvals)) if pvals else 0.0})
   if mi%40==0:print(json.dumps({'progressMarkets':mi,'total':len(snaps),'generated':len(gen)}),flush=True)
  sdf=pd.DataFrame(rows).sort_values(['window_end_ms','decision_ms']);gdf=pd.DataFrame(gen);mdf=pd.DataFrame(market_rows).sort_values('windowEndMs');sdf.to_csv(STATES,index=False);gdf.to_csv(EVENTS,index=False);mdf.to_csv(MARKETS,index=False)
  # Evaluate frozen SIDE/EFFECT at true Target event times using the bootstrapped OUR state.
  teacher=teacher_events();te=[];groups={int(m):g.sort_values('decision_ms') for m,g in sdf.groupby('target_market_id')}
  for _,e in teacher.iterrows():
   tm=int(e['market_id']);g=groups.get(tm)
   if g is None or g.empty:continue
   ts=g.decision_ms.astype('int64').to_numpy();t=int(e['checkpoint_ms']);j=int(np.searchsorted(ts,t,side='right')-1)
   if j<0 or t-int(ts[j])>2000:continue
   r=g.iloc[j];raw={f:r.get(f,math.nan) for f in set(hfs+sfs+efs)};sp=str(side_art['model'].predict(numeric_row(raw,sfs))[0]);ep=str(effect_art['model'].predict(numeric_row(raw,efs))[0]);te.append({'targetMarketId':tm,'targetParentId':str(e['parent_id']),'eventMs':t,'ourStateMs':int(ts[j]),'truthSide':str(e['label_side']),'predSide':sp,'truthEffect':str(e['label_effect']),'predEffect':ep,'pTaker1s':float(r.p_taker_1s)})
  tdf=pd.DataFrame(te);tdf.to_csv(TEVAL,index=False)
  actual=mdf.targetTakerParents.astype(float);stu=mdf.studentTakersTotal.astype(float);generated_sides=gdf.side.value_counts().to_dict() if len(gdf) else {};generated_eff=gdf.realizedEffect.value_counts().to_dict() if len(gdf) else {}
  rep={'reportVersion':'TARGET_BLIND_TAKER_BOOTSTRAP_V0','researchOnly':True,'runtimeTargetDataAllowed':False,'question':'Does stochastic self-generated Taker activity from frozen Target hazard+side students bootstrap OUR lifecycle toward the Target regime without any Target runtime input?','policy':{'hazard':'sample Bernoulli from frozen Target 1s hazard each recorder state','side':'sample from frozen Target SIDE probabilities','sizeShares':SHARES,'price':'current public ask','effect':'realized post-hoc from own combined net; effect model is not used to force action','maker':'unchanged frozen stable-directional replay','originalOPEN_SEED':'preserved as existing OUR seed','rngSeed':RNG_SEED},'coverage':{'markets':len(mdf),'states':len(sdf),'generatedTakers':len(gdf),'trueTargetEventComparisons':len(tdf),'dropped':drop},'activity':{'targetParentsTotal':int(actual.sum()),'originalOpenSeedsTotal':int(mdf.originalOpenSeeds.sum()),'generatedTakersTotal':int(mdf.generatedTakers.sum()),'studentTakersTotal':int(stu.sum()),'meanTargetPerMarket':float(actual.mean()),'meanStudentPerMarket':float(stu.mean()),'medianTargetPerMarket':float(actual.median()),'medianStudentPerMarket':float(stu.median()),'spearmanStudentVsTarget':float(stu.corr(actual,method='spearman')),'maeCount':float((stu-actual).abs().mean()),'generatedSideDistribution':generated_sides,'generatedRealizedEffectDistribution':generated_eff},'teacherEventTransferAfterBootstrap':{'side':multi(tdf.truthSide.astype(str).tolist(),tdf.predSide.astype(str).tolist()) if len(tdf) else None,'effect':multi(tdf.truthEffect.astype(str).tolist(),tdf.predEffect.astype(str).tolist()) if len(tdf) else None,'meanP1AtTrueTaker':float(tdf.pTaker1s.mean()) if len(tdf) else None,'medianP1AtTrueTaker':float(tdf.pTaker1s.median()) if len(tdf) else None},'baselineOwnStateReference':{'meanExpectedTakersPerMarket':2.6728,'sideBalancedAtTrueTaker':0.5965,'effectBalancedAtTrueTaker':0.3810,'meanP1AtTrueTaker':0.0157},'guards':['Single deterministic stochastic V0; do not infer robustness from one RNG seed.','No threshold tuning.','Target actions/effects are evaluation truth only; never runtime features.','Fixed 18-share Taker is a V0 execution proxy, not learned Target size.','Sequential Maker re-entry student is intentionally not enabled yet so this isolates Taker self-bootstrap.'],'artifacts':{'statesCsv':str(STATES),'generatedEventsCsv':str(EVENTS),'marketCsv':str(MARKETS),'targetEventEvalCsv':str(TEVAL)}};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
 finally:our.close();book.close();target.close()
if __name__=='__main__':raise SystemExit(main())
