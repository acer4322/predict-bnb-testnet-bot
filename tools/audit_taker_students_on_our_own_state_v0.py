from __future__ import annotations

import bisect, importlib.util, json, math, sqlite3, sys
from pathlib import Path
from typing import Any
import joblib,numpy as np,pandas as pd
from sklearn.metrics import accuracy_score,balanced_accuracy_score,f1_score

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'bridge_target_reentry_hazard_to_our_open_counterfactual_v0.py';spec=importlib.util.spec_from_file_location('ownstate_base',P);br=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=br;spec.loader.exec_module(br);base=br.base;mod=br.mod;coord=br.coord
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1';REPORT=OUT/'taker_students_on_our_own_state_v0_report.json';STATES=OUT/'taker_students_on_our_own_state_v0_markets.csv';EVENTS=OUT/'taker_students_on_our_own_state_v0_event_predictions.csv'
CONTRACT=OUT/'forward_contract_v1.json';HIST_T=OUT/'taker_event_states_v1.csv';FRESH_T=OUT/'forward_taker_states_v1.csv';BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db';TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'

def ro(path):
 c=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro',uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c

def score_binary(art,row):
 fs=art['features'];x=pd.DataFrame([{f:row.get(f,math.nan) for f in fs}]).apply(pd.to_numeric,errors='coerce');return float(art['model'].predict_proba(x)[0,1])
def predict_multi(art,row):
 fs=art['features'];x=pd.DataFrame([{f:row.get(f,math.nan) for f in fs}]).apply(pd.to_numeric,errors='coerce');return str(art['model'].predict(x)[0])
def multi(y,p):
 return {'n':len(y),'accuracy':float(accuracy_score(y,p)) if y else None,'balancedAccuracy':float(balanced_accuracy_score(y,p)) if y else None,'macroF1':float(f1_score(y,p,average='macro',zero_division=0)) if y else None,'truthDistribution':pd.Series(y).value_counts().to_dict() if y else {},'predictedDistribution':pd.Series(p).value_counts().to_dict() if y else {}}
def event_teacher():
 parts=[]
 for p in (HIST_T,FRESH_T):
  if p.exists():parts.append(pd.read_csv(p))
 if not parts:return pd.DataFrame()
 d=pd.concat(parts,ignore_index=True,sort=False);return d[['market_id','checkpoint_ms','parent_id','label_side','label_effect']].drop_duplicates('parent_id')
def book_updates(book,market):
 return list(book.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(market,)))
def main():
 contract=json.loads(CONTRACT.read_text(encoding='utf-8'));arts={k:joblib.load(v) for k,v in contract['artifacts'].items() if k in ('hazard_1s','side','effect')}
 our=mod.ro(mod.DEFAULT_OUR_DB);book=ro(BOOK_DB);target=ro(TARGET_DB)
 try:
  snaps=mod.load_snapshots(our);seedmap=mod.load_seeds(our);models=mod.maker_ebm.load_models();emap=br.first_book_market_by_end(book);teacher=event_teacher();teacher_by={m:g.sort_values('checkpoint_ms') for m,g in teacher.groupby('market_id')} if len(teacher) else {};market_rows=[];event_rows=[]
  for om in sorted(snaps):
   items=snaps[om]
   if not items:continue
   ws=int(mod.snapshot_value(dict(items[0]['snapshot']),'window_end_ms','windowEndMs') or 0);tm=emap.get(ws)
   if tm is None:continue
   ups=book_updates(book,tm);ui=0;state={'bids':{},'asks':{}};last=None;ref=base.DirectionalSim(models);seeds=list(seedmap.get(om,[]));si=0;scored=[]
   for idx,it in enumerate(items):
    now=int(it['decision_ms']);snap=dict(it['snapshot']);
    while ui<len(ups) and int(ups[ui]['source_timestamp_ms'])<=now:
     u=ups[ui]
     if int(u['is_checkpoint']):state={'bids':{float(k):float(v) for k,v in (coord.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (coord.dec(u['native_asks_z']) or {}).items()}}
     else:coord.apply_changes(state,coord.dec(u['changes_z']) or {})
     last=int(u['source_timestamp_ms']);ui+=1
    ns=int(mod.num(snap.get('timestampNs')) or mod.num(snap.get('timestamp_ns')) or now*1_000_000);filled=ref.fill_existing(snap,ns,now);dec=ref.decide(snap,om,now);sf=False
    while si<len(seeds) and int(seeds[si]['filled_at_ms'])<=now:ref.apply_seed(seeds[si]);si+=1;sf=True
    age=now-last if last is not None else 10**9
    if 0<=age<=2000:
     _,feat,cn,dom=br.own_inventory(ref,now);bf=coord.outcome_book(state,dom)
     if bf:
      raw={'seconds_left':(ws-now)/1000.0,**feat,**bf};p1=score_binary(arts['hazard_1s'],raw);scored.append({'ms':now,'p1':p1,'raw':raw})
    ref.apply_plan(dec,ns,now,allow_new=(filled==0 and not sf))
   if not scored:continue
   simple=sum(x['p1'] for x in scored);expo=0.0
   for i,x in enumerate(scored):
    dt=((scored[i+1]['ms']-x['ms'])/1000.0) if i+1<len(scored) else 1.0;lam=-math.log(max(1e-9,1.0-min(max(x['p1'],0.0),0.999999)));expo+=lam*max(dt,0.0)
   actual=int(target.execute("select count(*) from target_parent_orders where market_id=? and asset='BTC' and role='TAKER' and quote_type='BID'",(tm,)).fetchone()[0]);market_rows.append({'ourMarketId':om,'targetMarketId':tm,'windowEndMs':ws,'states':len(scored),'sumP1':simple,'exposureExpectedCount':expo,'targetTakerParents':actual,'meanP1':simple/len(scored)})
   tg=teacher_by.get(tm)
   if tg is not None:
    times=[x['ms'] for x in scored]
    for _,e in tg.iterrows():
     t=int(e['checkpoint_ms']);j=bisect.bisect_right(times,t)-1
     if j<0 or t-times[j]>2000:continue
     raw=scored[j]['raw'];event_rows.append({'ourMarketId':om,'targetMarketId':tm,'windowEndMs':ws,'targetParentId':str(e['parent_id']),'targetEventCheckpointMs':t,'ourStateMs':times[j],'stateAgeMs':t-times[j],'truthSide':str(e['label_side']),'predSide':predict_multi(arts['side'],raw),'truthEffect':str(e['label_effect']),'predEffect':predict_multi(arts['effect'],raw),'pTaker1sOnOurState':scored[j]['p1']})
  md=pd.DataFrame(market_rows);ed=pd.DataFrame(event_rows);md.to_csv(STATES,index=False);ed.to_csv(EVENTS,index=False)
  corr={}
  if len(md):
   corr={'markets':len(md),'targetParentsTotal':int(md.targetTakerParents.sum()),'sumP1Total':float(md.sumP1.sum()),'exposureExpectedTotal':float(md.exposureExpectedCount.sum()),'meanTargetPerMarket':float(md.targetTakerParents.mean()),'meanExpectedPerMarket':float(md.exposureExpectedCount.mean()),'spearmanExpectedVsActual':float(md.exposureExpectedCount.corr(md.targetTakerParents,method='spearman')),'pearsonExpectedVsActual':float(md.exposureExpectedCount.corr(md.targetTakerParents,method='pearson')),'maeExpectedCount':float((md.exposureExpectedCount-md.targetTakerParents).abs().mean())}
  rep={'reportVersion':'TAKER_STUDENTS_ON_OUR_OWN_STATE_V0','researchOnly':True,'question':'On the same public market tape, how well do frozen Target Taker students transfer when portfolio/lifecycle state is OUR own rather than Target teacher state?','coverage':{'markets':len(md),'targetEventComparisons':len(ed)},'hazardExpectedCount':corr,'sideOnOurStateAtTargetEventTimes':multi(ed.truthSide.tolist(),ed.predSide.tolist()) if len(ed) else None,'effectOnOurStateAtTargetEventTimes':multi(ed.truthEffect.tolist(),ed.predEffect.tolist()) if len(ed) else None,'meanP1AtTargetEventTimes':float(ed.pTaker1sOnOurState.mean()) if len(ed) else None,'artifacts':{'marketCsv':str(STATES),'eventCsv':str(EVENTS)},'interpretationBoundary':'Poor performance here means the student may be valid on Target teacher states but does not transfer to OUR endogenous portfolio/lifecycle distribution; domain adaptation is required before closed-loop. Good performance would justify target-blind stochastic replay next.'};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
 finally:our.close();book.close();target.close()
if __name__=='__main__':raise SystemExit(main())
