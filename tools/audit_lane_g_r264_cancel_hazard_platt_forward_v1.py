from __future__ import annotations
import argparse,json,math,os
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,brier_score_loss
try:
    import train_lane_g_r264_execution_world_v1 as wm
except Exception:
    from tools import train_lane_g_r264_execution_world_v1 as wm

LABELS=('cancelReq3s','cancelReq5s')

def clip(p):return np.clip(np.asarray(p,float),1e-6,1-1e-6)
def logit(p):
    p=clip(p);return np.log(p/(1-p)).reshape(-1,1)
def auc(y,p):return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def ece(y,p,bins=10):
    y=np.asarray(y,int);p=np.asarray(p,float);edges=np.linspace(0,1,bins+1);z=0.0
    for i in range(bins):
        mask=(p>=edges[i])&(p<(edges[i+1]) if i<bins-1 else p<=edges[i+1])
        n=int(mask.sum())
        if n:z+=(n/len(y))*abs(float(p[mask].mean())-float(y[mask].mean()))
    return float(z)
def metrics(y,p):return {'n':len(y),'positiveSupport':int(np.sum(y)),'auc':auc(y,p),'brier':float(brier_score_loss(y,p)),'ece10':ece(y,p)}
def fit_base(rows,label,seed):
    x=wm.X(rows,True);y=np.asarray([int(r[label]) for r in rows],int)
    return HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=15,l2_regularization=2.0,random_state=seed).fit(x,y)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--training-rows',required=True);ap.add_argument('--final-world-model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    rows=[json.loads(x) for x in Path(a.training_rows).read_text(encoding='utf-8').splitlines() if x.strip()]
    mids=[m for _,m in sorted({(int(r['windowEndMs']),int(r['marketId'])) for r in rows})]
    if len(mids)!=100:raise RuntimeError(len(mids))
    bymid={m:[r for r in rows if int(r['marketId'])==m] for m in mids}
    folds=[]
    # Fixed forward folds: prefix train, next 10 calibration, following 10 test.
    for train_n in (30,40,50,60,70,80):
        cal_m=mids[train_n:train_n+10];test_m=mids[train_n+10:train_n+20]
        if len(test_m)<10:continue
        folds.append({'trainN':train_n,'train':mids[:train_n],'cal':cal_m,'test':test_m})
    report={'version':'LANE_G_R264_CANCEL_HAZARD_PLATT_FORWARD_V1_20260907','researchOnly':True,'runtimeAuthority':False,'folds':[],'labels':{k:[] for k in LABELS}}
    oof={k:{'p':[],'y':[]} for k in LABELS}
    for fi,f in enumerate(folds):
        tr=[r for m in f['train'] for r in bymid[m]];ca=[r for m in f['cal'] for r in bymid[m]];te=[r for m in f['test'] for r in bymid[m]]
        fr={'fold':fi+1,'trainMarkets':len(f['train']),'calMarkets':10,'testMarkets':10,'trainRows':len(tr),'calRows':len(ca),'testRows':len(te),'labels':{}}
        for j,label in enumerate(LABELS):
            base=fit_base(tr,label,1000+fi*10+j);pca=base.predict_proba(wm.X(ca,True))[:,1];yca=np.asarray([int(r[label]) for r in ca]);pte=base.predict_proba(wm.X(te,True))[:,1];yte=np.asarray([int(r[label]) for r in te])
            platt=LogisticRegression(C=1.0,max_iter=1000,random_state=2000+fi*10+j).fit(logit(pca),yca);qte=platt.predict_proba(logit(pte))[:,1]
            raw=metrics(yte,pte);cal=metrics(yte,qte);d={'raw':raw,'platt':cal,'brierImprovement':raw['brier']-cal['brier'],'eceImprovement':raw['ece10']-cal['ece10'],'aucDelta':None if raw['auc'] is None else cal['auc']-raw['auc']}
            fr['labels'][label]=d;report['labels'][label].append(d)
            # Calibration block predictions are genuine forward predictions from a prefix-only model.
            oof[label]['p'].extend(map(float,pca));oof[label]['y'].extend(map(int,yca))
        report['folds'].append(fr);print(json.dumps({'fold':fi+1,'trainN':f['trainN'],'labels':{k:{'dBrier':v['brierImprovement'],'dECE':v['eceImprovement'],'dAUC':v['aucDelta']} for k,v in fr['labels'].items()}},ensure_ascii=False),flush=True)
    summary={}
    for label,z in report['labels'].items():
        summary[label]={'folds':len(z),'brierPositive':sum(x['brierImprovement']>0 for x in z),'ecePositive':sum(x['eceImprovement']>0 for x in z),'meanBrierImprovement':float(np.mean([x['brierImprovement'] for x in z])),'meanECEImprovement':float(np.mean([x['eceImprovement'] for x in z])),'maxAbsAucDelta':float(max(abs(x['aucDelta'] or 0.0) for x in z))}
    report['summary']=summary
    report['gates']={'brierImprovesAtLeast4of6Both':all(v['brierPositive']>=4 for v in summary.values()),'eceImprovesAtLeast4of6Both':all(v['ecePositive']>=4 for v in summary.values()),'meanBrierImprovementPositiveBoth':all(v['meanBrierImprovement']>0 for v in summary.values()),'rankingPreserved':all(v['maxAbsAucDelta']<=1e-12 for v in summary.values())}
    report['forwardPass']=all(report['gates'].values())
    # Build one fixed OOF Platt mapping from forward calibration-block predictions for application to the existing final70 world model.
    final_world=joblib.load(a.final_world_model);final_cal={}
    for label in LABELS:
        p=np.asarray(oof[label]['p'],float);y=np.asarray(oof[label]['y'],int);pl=LogisticRegression(C=1.0,max_iter=1000,random_state=3000).fit(logit(p),y);final_cal[label]=pl
    report['oofCalibrationRows']={k:len(v['y']) for k,v in oof.items()}
    report['boundary']=['fixed Platt only; no calibrator selection for cancel-request hazard','six chronological forward folds','each fold uses prefix train -> next10 calibration -> next10 test','Platt is strictly monotonic so ranking must be preserved','OOF Platt fit uses only forward calibration-block predictions from consumed H100','no fresh/no winner/no 8781']
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');joblib.dump({'version':report['version'],'calibrators':final_cal,'labels':LABELS,'logitInput':True},op.parent/'platt_oof_calibrators.joblib');print(json.dumps({'ok':True,'forwardPass':report['forwardPass'],'gates':report['gates'],'summary':summary},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
