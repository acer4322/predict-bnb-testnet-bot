from __future__ import annotations
import json, sqlite3, random
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score, accuracy_score, confusion_matrix

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data'/'research'/'r3_v0'
DB=ROOT/'data'/'target_wallet_official_v1.db'
import sys
sys.path.insert(0,str((ROOT/'tools').resolve()))
import train_r3_formation_sequence_v2 as seq

ARB=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')['model']
CROSS=joblib.load(R/'r3_safe_crossing_ebm_full_v1.joblib')['model']
EFEATURES=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')['features']

# Information-only R2.1 semantic outputs. No R3 action is encoded.
SCENARIOS=[
 ('NORMAL_FULL_FILL',0,1,0,0,0,0,1),
 ('LIVE_NO_FILL_DELAY_NOT_TERMINAL',1,0,1,0,0,0,0),
 ('LIVE_NO_FILL_STALL_CHILD_OWNS_REMAINDER',1,0,1,0,0,0,0),
 ('TERMINAL_ZERO_FILL_NEW_REPAIR_OBLIGATION',0,1,1,0,0,0,1),
 ('SUBMIT_REJECT_CONFIRMED_NEW_REPAIR_OBLIGATION',0,1,1,0,0,0,1),
 ('LIVE_PARTIAL_CHILD_STILL_OWNS_REMAINDER',1,0,1,0,0,0,0),
 ('LIVE_PARTIAL_STALL_DO_NOT_DUPLICATE_OWNER',1,0,1,0,0,0,0),
 ('PARTIAL_CHILD_REMAINDER_NEW_REPAIR_OBLIGATION',0,1,1,0,0,0,1),
 ('LATE_FILL_RECONCILE_EXISTING_CHILD',0,1,0,1,0,0,1),
 ('CANCEL_PENDING_OWNERSHIP_LOCKED',1,0,1,0,1,0,0),
 ('FILL_DURING_CANCEL_RECOMPUTE_AT_ACK',0,1,0,1,1,0,1),
 ('CANCEL_ACK_ZERO_FILL_RELEASE_TO_REPAIR',0,1,1,0,0,0,1),
 ('CANCEL_ACK_PARTIAL_RECOMPUTE_REMAINDER',0,1,1,0,0,0,1),
 ('UNKNOWN_SUBMISSION_THEN_RECONCILED',0,1,0,1,0,0,1),
 ('ORDER_STATE_UNKNOWN_OWNERSHIP_UNRELEASED',1,0,1,0,0,1,0),
 ('DUPLICATE_EVENT_IGNORE',1,0,0,0,0,0,2),
 ('OUT_OF_ORDER_EVENT_IGNORE',1,0,0,0,0,0,2),
 ('FILLED_CHILD_TARGET_MOVED_RECOMPUTE_FROM_ACTUAL_AND_NEW_TARGET',0,1,0,0,0,0,1),
 ('ACTIVE_REPAIR_FAILED_REJECT_REASSESS',0,1,1,0,0,0,1),
 ('ACTIVE_REPAIR_FAILED_NO_FILL_REASSESS',0,1,1,0,0,0,1),
 ('ACTIVE_REPAIR_PARTIAL_REMAINDER_REASSESS',0,1,1,0,0,0,1),
 ('LATE_OLD_FILL_RECONCILE_BEFORE_NEXT_REPAIR',1,0,1,1,0,0,0),
 ('TARGET_CHANGED_DURING_CANCEL_WAIT_ACK_THEN_RECOMPUTE',1,0,1,0,1,0,0),
 ('DATA_END_UNKNOWN_PRESERVE_OWNERSHIP',1,0,1,0,0,1,0),
]
# label: 0 HOLD, 1 TRUST, 2 IGNORE/KEEP_PRIOR

def build_checkpoint_rows(max_per_market=12):
 c=sqlite3.connect(DB)
 mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]
 out=[]
 for mid in mids:
  rows=seq.build_market(c,mid)
  if not rows: continue
  # deterministic subsample across each market to keep curriculum manageable
  if len(rows)>max_per_market:
   idx=np.linspace(0,len(rows)-1,max_per_market,dtype=int)
   rows=[rows[i] for i in idx]
  for m,t,x,y in rows:
   f=dict(zip(seq.FEATURES,x)); X=np.array([[float(f[k]) for k in EFEATURES]],float)
   pb=float(ARB.predict_proba(X)[0,1]); pc=float(CROSS.predict_proba(X)[0,1])
   out.append((m,t,y,pb,pc,float(abs(pb-.5)),float(abs(pc-.5))))
 c.close(); return out

def feature(cp,sc):
 mid,t,y,pb,pc,mb,mc=cp
 name,ownership_locked,terminal,remaining,reconcile,cancel_pending,unknown,label=sc
 # Only cooperation/reliability features plus R3 confidence; no target action label y.
 return [pb,pc,mb,mc,ownership_locked,terminal,remaining,reconcile,cancel_pending,unknown,
         float(ownership_locked and not terminal),float(reconcile and terminal),float(remaining and terminal),
         float(max(pb,pc)),float(abs(pb-pc))]

def main():
 cps=build_checkpoint_rows(); mids=sorted(set(x[0] for x in cps)); a=int(.6*len(mids)); b=int(.8*len(mids))
 trainm=set(mids[:a]); valm=set(mids[a:b]); testm=set(mids[b:])
 # Hold out selected scenario families entirely from training to test semantic generalization from R2.1 fields.
 holdout_names={'LIVE_PARTIAL_STALL_DO_NOT_DUPLICATE_OWNER','CANCEL_PENDING_OWNERSHIP_LOCKED','ACTIVE_REPAIR_PARTIAL_REMAINDER_REASSESS','OUT_OF_ORDER_EVENT_IGNORE','TARGET_CHANGED_DURING_CANCEL_WAIT_ACK_THEN_RECOMPUTE'}
 def make(ms,train=False):
  X=[]; Y=[]; names=[]
  for cp in cps:
   if cp[0] not in ms: continue
   for sc in SCENARIOS:
    if train and sc[0] in holdout_names: continue
    X.append(feature(cp,sc)); Y.append(sc[-1]); names.append(sc[0])
  return np.asarray(X,float),np.asarray(Y,int),names
 Xtr,ytr,ntr=make(trainm,True); Xv,yv,nv=make(valm,False); Xt,yt,nt=make(testm,False)
 model=HistGradientBoostingClassifier(max_iter=180,learning_rate=.07,max_leaf_nodes=15,min_samples_leaf=80,l2_regularization=3.0,random_state=20260824).fit(Xtr,ytr)
 def score(X,y,names):
  p=model.predict(X); rep={'n':len(y),'accuracy':float(accuracy_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,p)),'confusion':confusion_matrix(y,p,labels=[0,1,2]).tolist(),'bySituation':{}}
  for nm in sorted(set(names)):
   ix=np.array([x==nm for x in names]); yy=y[ix]; pp=p[ix]
   rep['bySituation'][nm]={'n':int(ix.sum()),'accuracy':float(accuracy_score(yy,pp)),'teacherLabel':int(yy[0])}
  return rep
 rep={'version':'R3_R21_COOPERATION_TRUST_V1','checkpointMarkets':len(mids),'baseCheckpoints':len(cps),'train':score(Xtr,ytr,ntr),'validation':score(Xv,yv,nv),'test':score(Xt,yt,nt),'heldOutSituationFamilies':sorted(holdout_names),'authority':{'r21ActionAuthority':False,'r3DecisionOwner':True},'labels':{'0':'HOLD_CURRENT_FORMATION_STATE','1':'TRUST_NEW_TRANSITION','2':'IGNORE_OBSERVATION_KEEP_PRIOR'},'featureNames':['r3_build_p','r3_cross_p','r3_build_margin','r3_cross_margin','r21_ownership_locked','r21_terminal_certainty','r21_remaining_obligation','r21_reconcile_needed','r21_cancel_pending','r21_unknown','r21_unsettled_owner','r21_terminal_reconciled','r21_terminal_remaining','r3_max_transition_p','r3_prob_gap']}
 joblib.dump({'model':model,'features':rep['featureNames'],'labels':rep['labels'],'authority':rep['authority']},R/'r3_r21_cooperation_trust_hgb_v1.joblib')
 (R/'r3_r21_cooperation_trust_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
 print(json.dumps({'version':rep['version'],'checkpointMarkets':rep['checkpointMarkets'],'baseCheckpoints':rep['baseCheckpoints'],'validation':{k:v for k,v in rep['validation'].items() if k!='bySituation'},'test':{k:v for k,v in rep['test'].items() if k!='bySituation'},'heldOutSituationFamilies':rep['heldOutSituationFamilies'],'heldoutTestAcc':{nm:rep['test']['bySituation'][nm]['accuracy'] for nm in sorted(holdout_names)}},indent=2))
if __name__=='__main__': main()
