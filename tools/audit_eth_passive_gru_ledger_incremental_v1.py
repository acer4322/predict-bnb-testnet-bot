from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
import numpy as np
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.train_eth_passive_maker_specialist_v1 as v1

def scores(model,z,task,dev):
    X,S,M,L,y=v1.arrays(z,task);logs=[]
    model.eval()
    with torch.no_grad():
        for i in range(0,len(y),4096):
            log=model(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(S[i:i+4096]).to(dev),torch.from_numpy(L[i:i+4096]).to(dev),task)
            logs.append(log.cpu().numpy())
    return np.concatenate(logs),L,np.asarray(y,int)

def met(y,p):
    p=np.asarray(p,float);y=np.asarray(y,int);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'auc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');a=ap.parse_args()
    rows,cut,nwin=v1.build(a.db);tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut];v1.standardize(tr,te)
    dev='cuda' if torch.cuda.is_available() else 'cpu';torch.manual_seed(v1.SEED);np.random.seed(v1.SEED)
    base=v1.train_model(v1.BaselineMemory(),tr,dev)
    tasks={};deltas={}
    for t in v1.TASKS:
        ltr,Ltr,ytr=scores(base,tr,t,dev);lte,Lte,yte=scores(base,te,t,dev)
        raw=1/(1+np.exp(-lte));rawm=met(yte,raw)
        one=LogisticRegression(C=.1,class_weight='balanced',max_iter=500,random_state=31).fit(ltr.reshape(-1,1),ytr)
        onep=one.predict_proba(lte.reshape(-1,1))[:,1];onem=met(yte,onep)
        Xtr=np.concatenate([ltr[:,None],Ltr],axis=1);Xte=np.concatenate([lte[:,None],Lte],axis=1)
        fus=LogisticRegression(C=.1,class_weight='balanced',max_iter=1000,random_state=31).fit(Xtr,ytr)
        fp=fus.predict_proba(Xte)[:,1];fm=met(yte,fp);d=fm['auc']-rawm['auc'];deltas[t]=d
        tasks[t]={'rawGru':rawm,'gruLogitOnlyCalibration':onem,'gruPlusLedgerLateFusion':fm,'aucDeltaFusionMinusRaw':d,'ledgerCoefficientL1':float(np.abs(fus.coef_[0][1:]).sum()),'gruCoefficient':float(fus.coef_[0][0])}
    mean=float(np.mean(list(deltas.values())));passed=sum(v>=.005 for v in deltas.values())>=2 and min(deltas.values())>=-.005 and mean>=.003
    out={'version':'ETH_PASSIVE_GRU_LEDGER_INCREMENTAL_V1','researchOnly':True,'device':dev,'chronologyCutoff':cut,'windows':nwin,'trainRows':len(tr),'testRows':len(te),'tasks':tasks,'aucDeltaFusionMinusRaw':deltas,'meanAucDelta':mean,'incrementalPass':bool(passed),'passRule':'at least 2/3 heads +0.005 AUC; no head below -0.005; mean >=0.003','decisionRule':'FAIL => ledger remains deterministic runtime control/accounting state, not a direct policy-model input','boundary':['ETH-only actual-filled Maker chronology','No BTC labels/gradients','No winner/future PnL','No TARGET_UNIT=18 or expected_parent_shares','Development architecture audit only']}
    rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'gru_ledger_incremental_v1.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'pass':bool(passed),'delta':deltas,'meanDelta':mean,'output':str(op)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
