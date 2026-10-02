from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r3_v0'; DB=ROOT/'data/target_wallet_official_v1.db'
import sys
sys.path.insert(0,str((ROOT/'tools').resolve()))
import train_r3_formation_sequence_v2 as seq
ARB=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')['model']
CROSS=joblib.load(R/'r3_safe_crossing_ebm_full_v1.joblib')['model']
EFEATURES=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')['features']

# name, owner_state(0=released,1=live,2=unknown), terminal, rem_base, prog_base, reconcile, cancel, unknown, valid
SC=[
 ('NORMAL_FULL_FILL',0,1,0.00,1.00,0,0,0,1),
 ('LIVE_NO_FILL_DELAY_NOT_TERMINAL',1,0,1.00,0.08,0,0,0,1),
 ('LIVE_NO_FILL_STALL_CHILD_OWNS_REMAINDER',1,0,1.00,0.03,0,0,0,1),
 ('TERMINAL_ZERO_FILL_NEW_REPAIR_OBLIGATION',0,1,1.00,0.00,0,0,0,1),
 ('SUBMIT_REJECT_CONFIRMED_NEW_REPAIR_OBLIGATION',0,1,1.00,0.00,0,0,0,1),
 ('LIVE_PARTIAL_CHILD_STILL_OWNS_REMAINDER',1,0,0.55,0.45,0,0,0,1),
 ('LIVE_PARTIAL_STALL_DO_NOT_DUPLICATE_OWNER',1,0,0.55,0.45,0,0,0,1),
 ('PARTIAL_CHILD_REMAINDER_NEW_REPAIR_OBLIGATION',0,1,0.45,0.55,0,0,0,1),
 ('LATE_FILL_RECONCILE_EXISTING_CHILD',0,1,0.20,0.80,1,0,0,1),
 ('CANCEL_PENDING_OWNERSHIP_LOCKED',1,0,0.85,0.15,0,1,0,1),
 ('FILL_DURING_CANCEL_RECOMPUTE_AT_ACK',1,0,0.35,0.65,1,1,0,1),
 ('CANCEL_ACK_ZERO_FILL_RELEASE_TO_REPAIR',0,1,1.00,0.00,0,0,0,1),
 ('CANCEL_ACK_PARTIAL_RECOMPUTE_REMAINDER',0,1,0.40,0.60,1,0,0,1),
 ('UNKNOWN_SUBMISSION_THEN_RECONCILED',0,1,0.10,0.90,1,0,0,1),
 ('ORDER_STATE_UNKNOWN_OWNERSHIP_UNRELEASED',2,0,1.00,0.00,0,0,1,1),
 ('DUPLICATE_EVENT_IGNORE',1,0,0.50,0.50,0,0,0,0),
 ('OUT_OF_ORDER_EVENT_IGNORE',1,0,0.50,0.50,0,0,0,0),
 ('FILLED_CHILD_TARGET_MOVED_RECOMPUTE_FROM_ACTUAL_AND_NEW_TARGET',0,1,0.00,1.00,1,0,0,1),
 ('ACTIVE_REPAIR_FAILED_REJECT_REASSESS',0,1,1.00,0.00,1,0,0,1),
 ('ACTIVE_REPAIR_FAILED_NO_FILL_REASSESS',0,1,1.00,0.00,1,0,0,1),
 ('ACTIVE_REPAIR_PARTIAL_REMAINDER_REASSESS',0,1,0.40,0.60,1,0,0,1),
 ('LATE_OLD_FILL_RECONCILE_BEFORE_NEXT_REPAIR',1,0,0.30,0.70,1,0,0,1),
 ('TARGET_CHANGED_DURING_CANCEL_WAIT_ACK_THEN_RECOMPUTE',1,0,0.70,0.30,1,1,0,1),
 ('DATA_END_UNKNOWN_PRESERVE_OWNERSHIP',2,0,1.00,0.00,0,0,1,1),
]
LABELS={0:'PRESERVE_CURRENT_REGIME',1:'ALLOW_R3_REEVALUATION',2:'IGNORE_CONTEXT_EVENT',3:'RECONCILE_THEN_REEVALUATE'}

def cps(max_per_market=8):
 c=sqlite3.connect(DB); mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; out=[]
 for mid in mids:
  rows=seq.build_market(c,mid)
  if not rows: continue
  if len(rows)>max_per_market:
   ix=np.linspace(0,len(rows)-1,max_per_market,dtype=int); rows=[rows[i] for i in ix]
  for m,t,x,y in rows:
   f=dict(zip(seq.FEATURES,x)); X=np.array([[float(f[k]) for k in EFEATURES]],float)
   pb=float(ARB.predict_proba(X)[0,1]); pc=float(CROSS.predict_proba(X)[0,1])
   current=2 if pc>=.35 else 1 if pb>=.48 else 0
   out.append({'mid':m,'t':t,'f':f,'pb':pb,'pc':pc,'current':current})
 c.close(); return out

def packet(cp,sc):
 name,owner,terminal,rem0,prog0,reconcile,cancel,unknown,valid=sc
 # deterministic contextual variation, so context types are not fixed numeric templates.
 jitter=((int(cp['mid'])%17)/16.0-.5)*.12
 rem=min(1.0,max(0.0,rem0+jitter if 0<rem0<1 else rem0))
 prog=min(1.0,max(0.0,prog0-jitter if 0<prog0<1 else prog0))
 age=float(200+(int(cp['t'])%7000))
 elapsed=float(100+(int(cp['mid'])%23)*125)
 # information-only belief: probability of passive progress on this obligation, not an action recommendation.
 passive=min(1.0,max(0.0,prog*(.85 if owner==1 else .65)+(.12 if reconcile else 0.0)))
 return {'contextType':name,'ownershipState':owner,'terminalCertainty':terminal,'remainingObligationFraction':rem,'passiveProgressProbability':passive,'reconcileNeeded':reconcile,'cancelPending':cancel,'unknownState':unknown,'eventValidity':valid,'observationAgeMs':age,'elapsedSincePriorMs':elapsed,'hasPriorObservation':1.0}

def label(p):
 if p['eventValidity']<.5: return 2
 if p['reconcileNeeded'] and (p['ownershipState']!=0 or not p['terminalCertainty']): return 0
 if p['reconcileNeeded']: return 3
 if p['ownershipState']!=0 or not p['terminalCertainty'] or p['unknownState'] or p['cancelPending']: return 0
 return 1

def vec(cp,p,interactions=True):
 f=cp['f']; pb,pc=cp['pb'],cp['pc']; conf=max(pb,pc); base=[
  float(cp['current']),pb,pc,abs(pb-.5),abs(pc-.5),
  float(f['floor']),float(f['floor_change_5s']),float(f['surplus_shares']),float(f['surplus_change_5s']),float(f['upside_change_5s']),
  float(p['ownershipState']),float(p['terminalCertainty']),float(p['remainingObligationFraction']),float(p['passiveProgressProbability']),float(p['reconcileNeeded']),float(p['cancelPending']),float(p['unknownState']),float(p['eventValidity']),p['observationAgeMs']/10000.,p['elapsedSincePriorMs']/5000.,p['hasPriorObservation']]
 if interactions:
  unreleased=float(p['ownershipState']!=0)
  base += [
   p['passiveProgressProbability']*p['remainingObligationFraction']*conf,
   unreleased*conf,
   p['terminalCertainty']*conf,
   p['reconcileNeeded']*conf,
   p['eventValidity']*conf,
   p['remainingObligationFraction']*min(abs(float(f['floor_change_5s'])),100.)/100.,
   p['passiveProgressProbability']*min(abs(float(f['surplus_change_5s'])),200.)/200.,
  ]
 return base

def main():
 C=cps(); mids=sorted(set(x['mid'] for x in C)); a=int(.6*len(mids)); b=int(.8*len(mids)); tr=set(mids[:a]); va=set(mids[a:b]); te=set(mids[b:])
 held={'LIVE_PARTIAL_STALL_DO_NOT_DUPLICATE_OWNER','FILL_DURING_CANCEL_RECOMPUTE_AT_ACK','OUT_OF_ORDER_EVENT_IGNORE','ACTIVE_REPAIR_PARTIAL_REMAINDER_REASSESS','TARGET_CHANGED_DURING_CANCEL_WAIT_ACK_THEN_RECOMPUTE','DATA_END_UNKNOWN_PRESERVE_OWNERSHIP'}
 def make(ms,train=False,interactions=True):
  X=[];Y=[];N=[]
  for cp in C:
   if cp['mid'] not in ms: continue
   for sc in SC:
    if train and sc[0] in held: continue
    p=packet(cp,sc); X.append(vec(cp,p,interactions));Y.append(label(p));N.append(sc[0])
  return np.asarray(X,float),np.asarray(Y,int),N
 def fit(interactions):
  Xtr,ytr,ntr=make(tr,True,interactions); Xv,yv,nv=make(va,False,interactions); Xt,yt,nt=make(te,False,interactions)
  model=HistGradientBoostingClassifier(max_iter=220,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=100,l2_regularization=3.0,random_state=20260824).fit(Xtr,ytr)
  def score(X,y,N):
   q=model.predict(X); out={'n':len(y),'accuracy':float(accuracy_score(y,q)),'balancedAccuracy':float(balanced_accuracy_score(y,q)),'confusion':confusion_matrix(y,q,labels=[0,1,2,3]).tolist(),'heldOut':{}}
   for nm in sorted(held):
    ix=np.asarray([z==nm for z in N]); out['heldOut'][nm]={'n':int(ix.sum()),'accuracy':float(accuracy_score(y[ix],q[ix])) if ix.any() else None}
   return out
  return model,score(Xv,yv,nv),score(Xt,yt,nt)
 plain,pv,pt=fit(False); mech,mv,mt=fit(True)
 names=['r3_current_regime','r3_build_p','r3_cross_p','r3_build_margin','r3_cross_margin','floor','floor_change_5s','surplus_shares','surplus_change_5s','upside_change_5s','r21_ownership_state','r21_terminal_certainty','r21_remaining_obligation_fraction','r21_passive_progress_probability','r21_reconcile_needed','r21_cancel_pending','r21_unknown','r21_event_validity','r21_observation_age_scaled','r21_elapsed_prior_scaled','r21_has_prior_observation','ctx_progress_x_remaining_x_conf','ctx_unreleased_x_conf','ctx_terminal_x_conf','ctx_reconcile_x_conf','ctx_validity_x_conf','ctx_remaining_x_floor_velocity','ctx_progress_x_surplus_velocity']
 rep={'version':'R3_R21_CONTEXT_ADAPTER_V2','markets':len(mids),'baseCheckpoints':len(C),'heldOutContextTypes':sorted(held),'packetContract':'R2.1 contextual information packet + R3-specific interpreter; contextType retained for audit but excluded from model vector','plainPacket':{'validation':pv,'test':pt},'mechanismAdapter':{'validation':mv,'test':mt},'labels':LABELS,'authority':{'r21ActionAuthority':False,'r3FormationActionOwner':True},'featureNames':names}
 joblib.dump({'model':mech,'features':names,'labels':LABELS,'contract':'R3_R21_CONTEXT_ADAPTER_V2','contextTypeUsedAsFeature':False,'r21ActionAuthority':False},R/'r3_r21_context_adapter_hgb_v2.joblib')
 (R/'r3_r21_context_adapter_v2_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
 print(json.dumps({'version':rep['version'],'markets':rep['markets'],'baseCheckpoints':rep['baseCheckpoints'],'plainTest':pt,'mechanismTest':mt,'contextTypeUsedAsFeature':False,'authority':rep['authority']},indent=2))
if __name__=='__main__': main()
