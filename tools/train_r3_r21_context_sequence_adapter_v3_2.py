from __future__ import annotations
import json, joblib, numpy as np
from pathlib import Path
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix
import sys
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r3_v0'
sys.path.insert(0,str((ROOT/'tools').resolve()))
import train_r3_r21_context_sequence_adapter_v3 as v3
import train_r3_r21_context_adapter_v2 as base
LABELS=base.LABELS
HELD={'active_partial_terminal_target_revision','cancel_partial_then_ack','out_of_order_after_terminal'}
def vec32(cp,p,prev_mode,prev_owner,prev_terminal,step):
    x=base.vec(cp,p,True)
    x += [float(prev_mode),float(prev_owner),float(prev_terminal),float(step),float(prev_mode==0),float(prev_mode==2),float(prev_mode==3)]
    x += [float(p['reconcileNeeded'] and p['ownershipState']!=0),float(p['reconcileNeeded'] and p['ownershipState']==0),float(p['terminalCertainty'] and prev_mode==0),float((not p['eventValidity']) and prev_terminal==1),float(p['terminalCertainty'] and p['remainingObligationFraction']>0)]
    released_now=float(p['ownershipState']==0 and p['terminalCertainty']==1)
    terminal_new_obligation=float(released_now and p['remainingObligationFraction']>0 and not p['reconcileNeeded'])
    stale_after_release=float((not p['eventValidity']) and prev_terminal==1 and prev_owner==0)
    released_after_unreleased=float(released_now and prev_owner!=0)
    authoritative_terminal=float(released_now and p['eventValidity']>0.5)
    x += [released_now,terminal_new_obligation,stale_after_release,released_after_unreleased,authoritative_terminal]
    return x

def make_rows(C,ms,train=False):
    X=[];Y=[];N=[]
    for cp in C:
        if cp['mid'] not in ms: continue
        for pattern, seq_names in v3.SEQS.items():
            if train and pattern in HELD: continue
            prev_mode=1; prev_owner=0; prev_terminal=1
            for step,nm in enumerate(seq_names):
                sc=next(s for s in base.SC if s[0]==nm); p=base.packet(cp,sc); y=base.label(p)
                X.append(vec32(cp,p,prev_mode,prev_owner,prev_terminal,step));Y.append(y);N.append(pattern)
                prev_mode=y; prev_owner=p['ownershipState']; prev_terminal=p['terminalCertainty']
    return np.asarray(X,float),np.asarray(Y,int),N

def main():
    C=base.cps(max_per_market=5); mids=sorted(set(x['mid'] for x in C)); a=int(.6*len(mids)); b=int(.8*len(mids)); tr=set(mids[:a]); va=set(mids[a:b]); te=set(mids[b:])
    Xtr,ytr,ntr=make_rows(C,tr,True); Xv,yv,nv=make_rows(C,va,False); Xt,yt,nt=make_rows(C,te,False)
    model=HistGradientBoostingClassifier(max_iter=260,learning_rate=.055,max_leaf_nodes=15,min_samples_leaf=100,l2_regularization=3.0,random_state=20260824).fit(Xtr,ytr)
    def score(X,y,N):
        q=model.predict(X); out={'n':len(y),'accuracy':float(accuracy_score(y,q)),'balancedAccuracy':float(balanced_accuracy_score(y,q)),'confusion':confusion_matrix(y,q,labels=[0,1,2,3]).tolist(),'byPattern':{}}
        for nm in sorted(set(N)):
            ix=np.asarray([z==nm for z in N]); out['byPattern'][nm]={'n':int(ix.sum()),'accuracy':float(accuracy_score(y[ix],q[ix]))}
        return out
    sv,st=score(Xv,yv,nv),score(Xt,yt,nt)
    rep={'version':'R3_R21_CONTEXT_SEQUENCE_ADAPTER_V3_2','markets':len(mids),'baseCheckpoints':len(C),'heldOutSequencePatterns':sorted(HELD),'validation':sv,'test':st,'mechanismMemoryAdditionsV32':['released_now','terminal_new_obligation','stale_after_release','released_after_unreleased','authoritative_terminal'],'authority':{'r21ActionAuthority':False,'r3FormationActionOwner':True},'note':'Held-out sequence names remain excluded. Added terminal-boundary/data-race mechanism memory only.'}
    joblib.dump({'model':model,'contract':'R3_R21_CONTEXT_SEQUENCE_ADAPTER_V3_2','labels':LABELS,'r21ActionAuthority':False},R/'r3_r21_context_sequence_adapter_hgb_v3_2.joblib')
    (R/'r3_r21_context_sequence_adapter_v3_2_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
