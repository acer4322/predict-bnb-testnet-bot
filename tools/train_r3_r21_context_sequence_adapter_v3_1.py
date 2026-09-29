from __future__ import annotations
import json
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix
ROOT=Path(__file__).resolve().parents[1]; R=ROOT/'data/research/r3_v0'
import sys; sys.path.insert(0,str((ROOT/'tools').resolve()))
import train_r3_r21_context_adapter_v2 as v2
import train_r3_r21_context_sequence_adapter_v3 as v3

def main():
 C=v2.cps(max_per_market=5); mids=sorted(set(x['mid'] for x in C)); a=int(.6*len(mids));b=int(.8*len(mids));tr=set(mids[:a]);va=set(mids[a:b]);te=set(mids[b:]);held={'cancel_partial_then_ack','active_partial_terminal_target_revision','out_of_order_after_terminal'}
 def rows(ms,training=False):
  X=[];Y=[];P=[]
  for cp in C:
   if cp['mid'] not in ms:continue
   for pname,names in v3.SEQ.items():
    if training and pname in held:continue
    prev_label=1;prev_owner=0;prev_terminal=1;step=0
    for nm in names:
     p=v2.packet(cp,v3.SCMAP[nm]);y=v2.label(p);x=v2.vec(cp,p,True);unreleased=float(p['ownershipState']!=0 or not p['terminalCertainty'])
     mem=[float(prev_label),float(prev_owner),float(prev_terminal),float(step),float(prev_label==0),float(prev_label==2),float(prev_label==3),
          float(p['reconcileNeeded'] and unreleased),float(p['reconcileNeeded'] and not unreleased),float(p['terminalCertainty'] and p['ownershipState']==0 and prev_label==0),float(p['eventValidity']==0 and prev_terminal==1),float(p['terminalCertainty'] and p['remainingObligationFraction']>0 and not p['reconcileNeeded'])]
     X.append(x+mem);Y.append(y);P.append(pname);prev_label=y;prev_owner=p['ownershipState'];prev_terminal=p['terminalCertainty'];step+=1
  return np.asarray(X,float),np.asarray(Y,int),P
 Xtr,ytr,_=rows(tr,True);Xv,yv,pv=rows(va);Xt,yt,pt=rows(te)
 m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.07,max_leaf_nodes=12,min_samples_leaf=80,l2_regularization=3,random_state=20260824).fit(Xtr,ytr)
 def score(X,y,P):
  q=m.predict(X);r={'n':len(y),'accuracy':float(accuracy_score(y,q)),'balancedAccuracy':float(balanced_accuracy_score(y,q)),'confusion':confusion_matrix(y,q,labels=[0,1,2,3]).tolist(),'byPattern':{}}
  for nm in sorted(set(P)):
   ix=np.asarray([z==nm for z in P]);r['byPattern'][nm]={'n':int(ix.sum()),'accuracy':float(accuracy_score(y[ix],q[ix]))}
  return r
 rep={'version':'R3_R21_CONTEXT_SEQUENCE_ADAPTER_V3_1','markets':len(mids),'baseCheckpoints':len(C),'heldOutSequencePatterns':sorted(held),'validation':score(Xv,yv,pv),'test':score(Xt,yt,pt),'mechanismMemoryAdditions':['reconcile_while_unreleased','reconcile_after_release','terminal_release_after_preserve','invalid_event_after_terminal','terminal_remaining_obligation'],'authority':{'r21ActionAuthority':False,'r3FormationActionOwner':True},'note':'Held-out sequence names remain excluded. Added only mechanism-level context-memory interactions inspired by the R2.1->R2 interpreter pattern.'}
 joblib.dump({'model':m,'contract':rep['version'],'r21ActionAuthority':False},R/'r3_r21_context_sequence_adapter_hgb_v3_1.joblib');(R/'r3_r21_context_sequence_adapter_v3_1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'validation':{k:v for k,v in rep['validation'].items() if k!='byPattern'},'test':{k:v for k,v in rep['test'].items() if k!='byPattern'},'heldOutTest':{k:rep['test']['byPattern'][k] for k in sorted(held)}},indent=2))
if __name__=='__main__':main()
