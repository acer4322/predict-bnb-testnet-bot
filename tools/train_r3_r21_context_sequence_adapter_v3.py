from __future__ import annotations
import json, joblib, numpy as np
from pathlib import Path
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix
import sys
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r3_v0'
sys.path.insert(0,str((ROOT/'tools').resolve()))
import train_r3_r21_context_adapter_v2 as base

# Sequence patterns are audit/curriculum labels only and are never used as model features.
SEQS={
 'partial_then_cancel_ack':['LIVE_PARTIAL_CHILD_STILL_OWNS_REMAINDER','CANCEL_PENDING_OWNERSHIP_LOCKED','CANCEL_ACK_PARTIAL_RECOMPUTE_REMAINDER'],
 'cancel_partial_then_ack':['CANCEL_PENDING_OWNERSHIP_LOCKED','FILL_DURING_CANCEL_RECOMPUTE_AT_ACK','CANCEL_ACK_PARTIAL_RECOMPUTE_REMAINDER'],
 'stall_then_late_fill':['LIVE_NO_FILL_STALL_CHILD_OWNS_REMAINDER','LATE_FILL_RECONCILE_EXISTING_CHILD'],
 'unknown_then_fill':['ORDER_STATE_UNKNOWN_OWNERSHIP_UNRELEASED','UNKNOWN_SUBMISSION_THEN_RECONCILED'],
 'unknown_then_timeout':['ORDER_STATE_UNKNOWN_OWNERSHIP_UNRELEASED','DATA_END_UNKNOWN_PRESERVE_OWNERSHIP'],
 'target_revision_then_fill':['TARGET_CHANGED_DURING_CANCEL_WAIT_ACK_THEN_RECOMPUTE','FILLED_CHILD_TARGET_MOVED_RECOMPUTE_FROM_ACTUAL_AND_NEW_TARGET'],
 'fill_then_target_revision':['NORMAL_FULL_FILL','FILLED_CHILD_TARGET_MOVED_RECOMPUTE_FROM_ACTUAL_AND_NEW_TARGET'],
 'duplicate_partial_then_ack':['LIVE_PARTIAL_CHILD_STILL_OWNS_REMAINDER','DUPLICATE_EVENT_IGNORE','CANCEL_ACK_PARTIAL_RECOMPUTE_REMAINDER'],
 'out_of_order_after_terminal':['TERMINAL_ZERO_FILL_NEW_REPAIR_OBLIGATION','OUT_OF_ORDER_EVENT_IGNORE'],
 'active_partial_terminal_target_revision':['ACTIVE_REPAIR_PARTIAL_REMAINDER_REASSESS','FILLED_CHILD_TARGET_MOVED_RECOMPUTE_FROM_ACTUAL_AND_NEW_TARGET'],
}
HELD={'active_partial_terminal_target_revision','cancel_partial_then_ack','out_of_order_after_terminal'}

def vec3(cp,p,prev_mode,prev_owner,prev_terminal,step):
    x=base.vec(cp,p,True)
    x += [float(prev_mode),float(prev_owner),float(prev_terminal),float(step),float(prev_mode==0),float(prev_mode==2),float(prev_mode==3)]
    return x

def make_rows(C,ms,train=False):
    X=[];Y=[];N=[]
    for cp in C:
        if cp['mid'] not in ms: continue
        for pattern,names in SEQS.items():
            if train and pattern in HELD: continue
            prev_mode=1; prev_owner=0; prev_terminal=1
            for step,nm in enumerate(names):
                sc=next(s for s in base.SC if s[0]==nm); p=base.packet(cp,sc); y=base.label(p)
                X.append(vec3(cp,p,prev_mode,prev_owner,prev_terminal,step));Y.append(y);N.append(pattern)
                prev_mode=y; prev_owner=p['ownershipState']; prev_terminal=p['terminalCertainty']
    return np.asarray(X,float),np.asarray(Y,int),N

def main():
    C=base.cps(max_per_market=5); mids=sorted(set(x['mid'] for x in C)); a=int(.6*len(mids)); b=int(.8*len(mids)); tr=set(mids[:a]);va=set(mids[a:b]);te=set(mids[b:])
    Xtr,ytr,ntr=make_rows(C,tr,True);Xv,yv,nv=make_rows(C,va,False);Xt,yt,nt=make_rows(C,te,False)
    model=HistGradientBoostingClassifier(max_iter=240,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=100,l2_regularization=3.0,random_state=20260824).fit(Xtr,ytr)
    def score(X,y,N):
        q=model.predict(X); out={'n':len(y),'accuracy':float(accuracy_score(y,q)),'balancedAccuracy':float(balanced_accuracy_score(y,q)),'confusion':confusion_matrix(y,q,labels=[0,1,2,3]).tolist(),'byPattern':{}}
        for nm in sorted(set(N)):
            ix=np.asarray([z==nm for z in N]); out['byPattern'][nm]={'n':int(ix.sum()),'accuracy':float(accuracy_score(y[ix],q[ix]))}
        return out
    rep={'version':'R3_R21_CONTEXT_SEQUENCE_ADAPTER_V3','markets':len(mids),'baseCheckpoints':len(C),'heldOutSequencePatterns':sorted(HELD),'validation':score(Xv,yv,nv),'test':score(Xt,yt,nt),'authority':{'r21ActionAuthority':False,'r3FormationActionOwner':True},'memoryFeatures':['previousCooperationMode','previousOwnershipState','previousTerminalCertainty','sequenceStep','previousWasPreserve','previousWasIgnore','previousWasReconcile'],'note':'Sequence model sees semantic packet + prior cooperation memory; contextType and sequencePattern are audit labels only, not model features.'}
    joblib.dump({'model':model,'contract':'R3_R21_CONTEXT_SEQUENCE_ADAPTER_V3','labels':base.LABELS,'r21ActionAuthority':False},R/'r3_r21_context_sequence_adapter_hgb_v3.joblib')
    (R/'r3_r21_context_sequence_adapter_v3_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
