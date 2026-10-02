from __future__ import annotations
import json,lzma,sys
from pathlib import Path
from collections import defaultdict
import numpy as np,pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as sim
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
from tools.build_r4_management_simulator_objective_memory_episodes_v5 import objctx,histctx
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';D=P/'r4_p0b_stage3_group_context_dataset_v1.csv';OUT=P/'r4_p0b_stage3_ownership_continuity_role_context_v1.json'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json']
ECON={'PARTIAL_FILL','FULL_FILL'}
def load_rows(ps):
 out=[]
 for p in ps:out+=json.loads(p.read_text(encoding='utf-8'))['rows']
 return out
def sr(r,dom):
 sl=float(r.get('secondsLeft') or r.get('seconds_left') or 0);base=[sl,float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0),1. if dom else 0.]
 if not dom:return base+[float(r.get('weakResponsibilityCount') or 0),float(r.get('sameSideActiveObjectives') or 0),float(r.get('sameSideResidualQty') or 0),float(r.get('sameSideReservedQty') or 0),float(r.get('sameSideConfirmedQty') or 0),float(r.get('oldestSameSideObjectiveAgeS') or 0),float(r.get('timeSinceLastWeakFillS') or 999),float(r.get('weakFillQty5s') or 0),float(r.get('weakFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]
 return base+[float(r.get('dominantResponsibilityCount') or 0),float(r.get('oppositeSideActiveObjectives') or 0),float(r.get('oppositeSideResidualQty') or 0),float(r.get('oppositeSideReservedQty') or 0),float(r.get('oppositeSideConfirmedQty') or 0),float(r.get('oldestOppositeSideObjectiveAgeS') or 0),float(r.get('timeSinceLastDominantFillS') or 999),float(r.get('dominantFillQty5s') or 0),float(r.get('dominantFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]
def lab(r,dom):
 rel='DOMINANT' if dom else 'WEAK';es=[e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=5000];opens={str(e.get('responsibilityId')):int(e['dtMs']) for e in es if e['eventType']=='RESPONSIBILITY_OPENED' and e.get('responsibilityId')}
 for e in es:
  if e['eventType'] not in ECON or e.get('sideRelation')!=rel:continue
  op=opens.get(str(e.get('responsibilityId'))) if e.get('responsibilityId') else None
  if op is None or op>=int(e['dtMs']):return 1
 return 0
def main():
 dev=load_rows(DEV);X=np.asarray([sr(r,d) for r in dev for d in (False,True)],float);y=np.asarray([lab(r,d) for r in dev for d in (False,True)],int);m=RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=8205,n_jobs=1).fit(X,y);tab=pd.read_csv(D);rows=[]
 for _,q in tab.iterrows():
  mid=int(q.marketId);key=str(q.candidateKey);d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key);c=next((x for x in r.get('managementShadowRows',[]) if str(x.get('kind'))=='SUCCESSOR_OPTION' and int(x.get('t'))==int(key.split('|',1)[0])),None)
  if c is None:continue
  pref=(r.get('provenanceJournal') or [])[:int(c.get('provenanceJournalCursor') or 0)];oj,ostate,resp_to_obj=led.materialize(mid,pref);weak=str(c.get('weakSide') or '');rid=(c.get('weakResponsibilityIds') or c.get('dominantResponsibilityIds') or [c.get('submittedResponsibilityId') or 'NONE'])[0];z=dict(c);z.update(objctx(mid,str(rid),int(c['t']),weak,oj,resp_to_obj));z.update(histctx(pref,oj,resp_to_obj,str(rid),int(c['t']),weak,weak));pw,pdom=map(float,m.predict_proba(np.asarray([sr(z,False),sr(z,True)],float))[:,1]);rows.append({'marketId':mid,'candidateKey':key,'knownRole':str(q.knownRole),'gateA_label':str(q.gateA_label),'gateB_label':None if pd.isna(q.gateB_label) else str(q.gateB_label),'pExistingWeak5s':pw,'pExistingDom5s':pdom,'weakLiveOwners':float(c.get('weakResponsibilityCount') or 0),'domLiveOwners':float(c.get('dominantResponsibilityCount') or 0),'sameSideReservedQty':float(z.get('sameSideReservedQty') or 0),'sameSideResidualQty':float(z.get('sameSideResidualQty') or 0),'oldestSameSideObjectiveAgeS':float(z.get('oldestSameSideObjectiveAgeS') or 0)})
 by={}
 for role in sorted(set(r['knownRole'] for r in rows)):
  a=[r for r in rows if r['knownRole']==role];by[role]={'n':len(a),'meanPExistingWeak5s':float(np.mean([r['pExistingWeak5s'] for r in a])),'medianPExistingWeak5s':float(np.median([r['pExistingWeak5s'] for r in a])),'highContinuity05':sum(r['pExistingWeak5s']>=.5 for r in a),'meanWeakLiveOwners':float(np.mean([r['weakLiveOwners'] for r in a]))}
 yrej=np.asarray([1 if r['knownRole']=='REJECT_NO_ACTION' else 0 for r in rows]);p=np.asarray([r['pExistingWeak5s'] for r in rows]);yre=np.asarray([1 if r['knownRole']!='REJECT_NO_ACTION' else 0 for r in rows]);auc_rej=float(roc_auc_score(yrej,p)) if len(set(yrej))>1 else None;auc_realize_low=float(roc_auc_score(yre,1-p)) if len(set(yre))>1 else None
 rep={'version':'R4_P0B_STAGE3_OWNERSHIP_CONTINUITY_ROLE_CONTEXT_V1','researchOnly':True,'promotionEvidence':False,'rows':len(rows),'roleSummary':by,'aucHighContinuityForReject':auc_rej,'aucLowContinuityForRealize':auc_realize_low,'rowsDetail':rows,'interpretation':'Consumed Lane-F role candidates only. Ownership Continuity is tested as strict-past role context, not as an admission gate. No threshold tuning or promotion.'};OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({k:rep[k] for k in ['rows','roleSummary','aucHighContinuityForReject','aucLowContinuityForRealize']},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
