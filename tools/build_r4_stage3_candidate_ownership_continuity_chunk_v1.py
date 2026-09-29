from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
from collections import defaultdict
import numpy as np,pandas as pd
from sklearn.ensemble import RandomForestClassifier
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as sim
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
from tools.build_r4_management_simulator_objective_memory_episodes_v5 import objctx,histctx
from tools.diagnose_r4_management_ownership_continuity_shared_head_v1 import stream_row,label
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';DATA=P/'r4_p0b_stage3_expanded48_dataset_v2.csv';EPS=1e-9
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json']

def load_dev():
 out=[]
 for p in DEV:out+=json.loads(p.read_text(encoding='utf-8'))['rows']
 return out

def fit_heads(dev):
 out={}
 for h in (5,15,30):
  X=np.asarray([stream_row(r,d) for r in dev for d in (False,True)],float);y=np.asarray([label(r,h,d) for r in dev for d in (False,True)],int)
  m=RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=8200+h,n_jobs=1).fit(X,y);out[h]=m
 return out

def open_roots(pref,weak):
 by=defaultdict(list)
 for e in pref:
  rid=e.get('responsibility_id')
  if rid:by[str(rid)].append(e)
 w=set();d=set();allside={}
 for rid,es in by.items():
  op=next((e for e in es if e.get('event_type')=='RESPONSIBILITY_OPENED'),None)
  if op is None:continue
  if any(e.get('event_type') in {'RESPONSIBILITY_COMPLETED','RESPONSIBILITY_TERMINATED'} for e in es):continue
  req=float(op.get('requested_qty') or 0);filled=sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0) for e in es if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'})
  if req-filled<=EPS:continue
  side=str(op.get('side') or '');allside[rid]=side
  if side==weak:w.add(rid)
  elif side in {'UP','DOWN'}:d.add(rid)
 return w,d,allside

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args()
 d=pd.read_csv(DATA).reset_index(drop=True);sub=d.iloc[a.start:a.start+a.count];heads=fit_heads(load_dev());out=[]
 for _,r in sub.iterrows():
  mid=int(r.marketId);key=str(r.candidateKey);t=int(r.candidateT);weak=str(r.weakSide);side=str(r.candidateSide)
  try:
   z=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
   rr=sim.simulate(z,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
   prov=rr.get('provenanceJournal') or []
   # Conservative strict-past prefix: exclude all events with receipt time equal to the candidate timestamp.
   pref=[e for e in prov if int(e.get('received_at_ms') or 0)<t]
   oj,_,resp_to_obj=led.materialize(mid,pref);wids,dids,sides=open_roots(pref,weak);candidate_ids=wids if side==weak else dids if side in {'UP','DOWN'} else set();rid=next(iter(candidate_ids),next(iter(wids or dids),'NONE'))
   base={'secondsLeft':float(r.seconds_left or 0),'floor':float(r.floor or 0),'absNet':float(r.absNet or r.abs_gap or 0),'coverage':float(r.coverage or 0),'weakSide':weak,'dominantSide':str(r.dominantSide),'weakResponsibilityCount':float(len(wids)),'dominantResponsibilityCount':float(len(dids))}
   base.update(objctx(mid,str(rid),t,weak,oj,resp_to_obj));base.update(histctx(pref,oj,resp_to_obj,str(rid),t,weak,weak))
   def probs(dom):
    roots=dids if dom else wids
    if not roots:return {str(h):0.0 for h in (5,15,30)}
    x=np.asarray([stream_row(base,dom)],float)
    return {str(h):float(heads[h].predict_proba(x)[0,1]) for h in (5,15,30)}
   pw=probs(False);pdom=probs(True);dom=bool(side!=weak);pc=pdom if dom else pw
   q={'marketId':mid,'candidateKey':key,'candidateT':t,'candidateSide':side,'weakSide':weak,'existingWeakResponsibilityCount':len(wids),'existingDominantResponsibilityCount':len(dids),'existingCandidateResponsibilityCount':len(candidate_ids),'pExistingWeak5s':pw['5'],'pExistingWeak15s':pw['15'],'pExistingWeak30s':pw['30'],'pExistingDominant5s':pdom['5'],'pExistingDominant15s':pdom['15'],'pExistingDominant30s':pdom['30'],'pExistingCandidate5s':pc['5'],'pExistingCandidate15s':pc['15'],'pExistingCandidate30s':pc['30'],'strictPastPrefixEvents':len(pref),'objectiveJournalEvents':len(oj)}
   out.append(q);print(json.dumps({'marketId':mid,'role':str(r.knownRole),'candidateRoots':len(candidate_ids),'p5':q['pExistingCandidate5s'],'p15':q['pExistingCandidate15s'],'p30':q['pExistingCandidate30s']}),flush=True)
  except Exception as ex:
   q={'marketId':mid,'candidateKey':key,'error':f'{type(ex).__name__}:{ex}'};out.append(q);print(json.dumps(q),flush=True)
 path=P/f'r4_stage3_candidate_ownership_continuity_chunk_{a.start}_{a.count}_v1.json';path.write_text(json.dumps({'version':'R4_STAGE3_CANDIDATE_OWNERSHIP_CONTINUITY_CHUNK_V1','start':a.start,'count':len(sub),'rows':out},indent=2),encoding='utf-8');print(json.dumps({'artifact':str(path.relative_to(ROOT)),'rows':len(out),'errors':sum('error' in x for x in out)}))
if __name__=='__main__':main()
