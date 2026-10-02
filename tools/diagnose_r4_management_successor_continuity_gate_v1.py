from __future__ import annotations
import json,lzma,sys
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_legitimate_successor_simulator_v1 as succ
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
from tools.build_r4_management_simulator_objective_memory_episodes_v5 import objctx,histctx
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';OUT=P/'r4_management_successor_continuity_gate_diagnostic_v1.json'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json']
ECON={'PARTIAL_FILL','FULL_FILL'}

def load_rows(paths):
 out=[]
 for p in paths:out+=json.loads(p.read_text(encoding='utf-8'))['rows']
 return out

def sr(r,dom):
 sl=float(r.get('secondsLeft') or r.get('seconds_left') or 0);base=[sl,float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0),1. if dom else 0.]
 if not dom:return base+[float(r.get('weakResponsibilityCount') or 0),float(r.get('sameSideActiveObjectives') or 0),float(r.get('sameSideResidualQty') or 0),float(r.get('sameSideReservedQty') or 0),float(r.get('sameSideConfirmedQty') or 0),float(r.get('oldestSameSideObjectiveAgeS') or 0),float(r.get('timeSinceLastWeakFillS') or 999),float(r.get('weakFillQty5s') or 0),float(r.get('weakFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]
 return base+[float(r.get('dominantResponsibilityCount') or 0),float(r.get('oppositeSideActiveObjectives') or 0),float(r.get('oppositeSideResidualQty') or 0),float(r.get('oppositeSideReservedQty') or 0),float(r.get('oppositeSideConfirmedQty') or 0),float(r.get('oldestOppositeSideObjectiveAgeS') or 0),float(r.get('timeSinceLastDominantFillS') or 999),float(r.get('dominantFillQty5s') or 0),float(r.get('dominantFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]

def lab_dev(r,dom):
 rel='DOMINANT' if dom else 'WEAK';es=[e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=5000];opens={str(e.get('responsibilityId')):int(e['dtMs']) for e in es if e['eventType']=='RESPONSIBILITY_OPENED' and e.get('responsibilityId')}
 for e in es:
  if e['eventType'] not in ECON or e.get('sideRelation')!=rel:continue
  rid=e.get('responsibilityId');op=opens.get(str(rid)) if rid else None
  if op is None or op>=int(e['dtMs']):return 1
 return 0

def eligible_findings():
 rows=[]
 for p in [P/'r4_p0b_parallel_successor_belief_chunk_0_5.json',P/'r4_p0b_parallel_successor_belief_chunk_5_5.json']:
  rows+=json.loads(p.read_text(encoding='utf-8'))['rows']
 return rows

def main():
 dev=load_rows(DEV);X=np.asarray([sr(r,d) for r in dev for d in (False,True)],float);y=np.asarray([lab_dev(r,d) for r in dev for d in (False,True)],int);m=RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=8205,n_jobs=1).fit(X,y)
 out=[]
 for base in eligible_findings():
  mid=int(base['marketId']);d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));v=succ.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);sr0=next((r for r in v.get('managementShadowRows',[]) if str(r.get('kind'))=='SUCCESSOR_OPTION'),None)
  if sr0 is None:continue
  journal=v.get('provenanceJournal') or [];cursor=int(sr0.get('provenanceJournalCursor') or 0);pref=journal[:cursor];oj,ostate,resp_to_obj=led.materialize(mid,pref);weak=str(sr0.get('weakSide') or '');rid=(sr0.get('weakResponsibilityIds') or sr0.get('dominantResponsibilityIds') or [sr0.get('submittedResponsibilityId') or 'NONE'])[0];z=dict(sr0);z.update(objctx(mid,str(rid),int(sr0['t']),weak,oj,resp_to_obj));z.update(histctx(pref,oj,resp_to_obj,str(rid),int(sr0['t']),weak,weak));pw=float(m.predict_proba(np.asarray([sr(z,False)],float))[0,1]);block=bool(pw>=0.5);out.append({**base,'pExistingWeak5s':pw,'continuityBlocksAtFrozen05':block,'weakResponsibilityCount':float(z.get('weakResponsibilityCount') or 0),'sameSideReservedQty':float(z.get('sameSideReservedQty') or 0),'sameSideResidualQty':float(z.get('sameSideResidualQty') or 0),'oldestSameSideObjectiveAgeS':float(z.get('oldestSameSideObjectiveAgeS') or 0)})
 harm=[r for r in out if int(r['floorHarm'])==1];safe=[r for r in out if int(r['floorHarm'])==0];yy=np.asarray([int(r['floorHarm']) for r in out]);pp=np.asarray([float(r['pExistingWeak5s']) for r in out]);auc=float(roc_auc_score(yy,pp)) if len(set(yy))>1 else None;blocked=[r for r in out if r['continuityBlocksAtFrozen05']];avoided=-sum(min(0.0,float(r['floorDeltaVsReservation'])) for r in blocked)
 rep={'version':'R4_MANAGEMENT_SUCCESSOR_CONTINUITY_GATE_DIAGNOSTIC_V1','researchOnly':True,'promotionEvidence':False,'rows':len(out),'harmRows':len(harm),'safeRows':len(safe),'pExistingWeak5s':{'harmMean':float(np.mean([r['pExistingWeak5s'] for r in harm])) if harm else None,'safeMean':float(np.mean([r['pExistingWeak5s'] for r in safe])) if safe else None,'harmMedian':float(np.median([r['pExistingWeak5s'] for r in harm])) if harm else None,'safeMedian':float(np.median([r['pExistingWeak5s'] for r in safe])) if safe else None,'aucForFloorHarm':auc},'frozen05Gate':{'blockedRows':len(blocked),'harmBlocked':sum(int(r['floorHarm']) for r in blocked),'safeBlocked':sum(1-int(r['floorHarm']) for r in blocked),'harmPassed':sum(int(r['floorHarm']) for r in out if not r['continuityBlocksAtFrozen05']),'safePassed':sum(1-int(r['floorHarm']) for r in out if not r['continuityBlocksAtFrozen05']),'floorHarmAvoidedIfBlockedRevertsToReservation':avoided},'rowsDetail':out,'interpretation':'Consumed Late60h diagnostic only. Continuity may suppress redundant successor opening; it cannot authorize a successor. No threshold sweep or promotion.'}
 OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({k:rep[k] for k in ['rows','harmRows','safeRows','pExistingWeak5s','frozen05Gate']},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
