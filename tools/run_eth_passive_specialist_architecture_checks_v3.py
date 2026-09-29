from __future__ import annotations
import argparse,json,os,sys
from copy import deepcopy
from pathlib import Path
import numpy as np
import torch
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.train_eth_passive_lifecycle_memory_feasibility_v2 as v2

SEED=20260831

def thin(z,n=7000):
    if len(z)<=n:return z
    idx=np.linspace(0,len(z)-1,n).astype(int)
    return [z[int(i)] for i in idx]

def truncate_history(rows,keep_last):
    out=deepcopy(rows)
    if keep_last>=v2.SEQ:return out
    for r in out:
        m=r['mask'].copy();s=r['seq'].copy();valid=np.where(m>0)[0]
        if len(valid)>keep_last:
            drop=valid[:-keep_last] if keep_last>0 else valid
            m[drop]=0;s[drop]=0
        r['mask']=m;r['seq']=s
    return out

def quick_mem(tr,te,keep_last,dev):
    tr=truncate_history(thin(tr),keep_last);te=truncate_history(thin(te),keep_last)
    v2.standardize(tr,te)
    torch.manual_seed(SEED);np.random.seed(SEED)
    m=v2.Memory().to(dev);opt=torch.optim.AdamW(m.parameters(),lr=1.2e-3,weight_decay=1e-4)
    rng=np.random.default_rng(SEED);pools={t:v2.arrays(tr,t) for t in v2.TASKS}
    for _ in range(4):
        for __ in range(50):
            opt.zero_grad();loss=0
            for t in v2.TASKS:
                X,S,M,y=pools[t];idx=rng.integers(0,len(y),size=min(256,len(y)))
                xt=torch.from_numpy(X[idx]).to(dev);st=torch.from_numpy(S[idx]).to(dev);mt=torch.from_numpy(M[idx]).to(dev);yt=torch.from_numpy(y[idx]).to(dev)
                pos=max(float(yt.mean()),1e-4);log=m(xt,st,mt,t)
                loss+=torch.nn.functional.binary_cross_entropy_with_logits(log,yt,pos_weight=torch.tensor((1-pos)/pos,device=dev).clamp(.25,4))
            loss.backward();torch.nn.utils.clip_grad_norm_(m.parameters(),5);opt.step()
    return v2.evaluate(m,te,dev,True)

def ledger_features(r):
    s=r['seq'];m=r['mask']>0;z=s[m]
    if len(z)==0:return np.zeros(12,np.float32)
    rel=z[:,0];dab=z[:,1];dpc=z[:,2];dfl=z[:,3];elapsed=z[:,4];qgr=z[:,5]
    last=float(rel[-1]);streak=0
    for x in rel[::-1]:
        if x==last:streak+=1
        else:break
    repair=(rel==1);expand=(rel==-1)
    repair_progress=float(np.maximum(0,-dab[repair]).sum()) if repair.any() else 0.
    repair_pair_gain=float(np.maximum(0,dpc[repair]).sum()) if repair.any() else 0.
    repair_floor_gain=float(np.maximum(0,dfl[repair]).sum()) if repair.any() else 0.
    expand_pressure=float(np.maximum(0,-dpc[expand]).sum()) if expand.any() else 0.
    switches=float(np.sum(rel[1:]!=rel[:-1])) if len(rel)>1 else 0.
    return np.asarray([last,streak/8.,float(repair.mean()),float(expand.mean()),repair_progress,repair_pair_gain,repair_floor_gain,expand_pressure,switches/7.,float(elapsed[-1]),float(np.mean(elapsed)),float(np.mean(qgr))],np.float32)

def met(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def ledger_eval(tr,te,task,use_ledger):
    a=[r for r in tr if r[task] is not None];b=[r for r in te if r[task] is not None]
    def X(rows):
        base=np.stack([r['cur'] for r in rows])
        if not use_ledger:return base
        return np.concatenate([base,np.stack([ledger_features(r) for r in rows])],axis=1)
    Xtr,Xte=X(a),X(b);ytr=np.asarray([r[task] for r in a],int);yte=np.asarray([r[task] for r in b],int)
    if len(Xtr)>50000:
        ii=np.linspace(0,len(Xtr)-1,50000).astype(int);Xtr=Xtr[ii];ytr=ytr[ii]
    m=HistGradientBoostingClassifier(max_iter=140,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=4.,class_weight='balanced',random_state=31)
    m.fit(Xtr,ytr);return met(yte,m.predict_proba(Xte)[:,1])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');a=ap.parse_args()
    rows,cut,nwin=v2.build(a.db);tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut]
    dev='cuda' if torch.cuda.is_available() else 'cpu'
    depth={}
    for k in (1,4,8):depth[f'last{k}']=quick_mem(tr,te,k,dev)
    ledger={}
    for t in v2.TASKS:
        s=ledger_eval(tr,te,t,False);l=ledger_eval(tr,te,t,True);ledger[t]={'static':s,'staticPlusLedger':l,'aucDelta':l['auc']-s['auc']}
    depth_delta={t:depth['last8'][t]['auc']-depth['last1'][t]['auc'] for t in v2.TASKS}
    ledger_delta={t:ledger[t]['aucDelta'] for t in v2.TASKS}
    out={'version':'ETH_PASSIVE_SPECIALIST_ARCHITECTURE_CHECKS_V3','researchOnly':True,'device':dev,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'trainRows':len(tr),'testRows':len(te),'memoryDepth':depth,'memoryDepthAucDeltaLast8MinusLast1':depth_delta,'objectiveLedger':ledger,'objectiveLedgerAucDelta':ledger_delta,'boundary':['ETH Target actual-filled Maker chronology only','No BTC labels or gradients','No winner/future PnL','No TARGET_UNIT=18 or inferred expected parent shares','Consumed Fresh101 is not used as graduation evidence','Architecture check only; not HFT profitability evidence']}
    out_path=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR',ROOT/'data/research/r4_v0/p0_provenance_v1'))/'architecture_checks_v3.json'
    out_path.parent.mkdir(parents=True,exist_ok=True);out_path.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'device':dev,'depthDelta':depth_delta,'ledgerDelta':ledger_delta,'output':str(out_path)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
