from __future__ import annotations
import argparse,json,math,os
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score,brier_score_loss

try:
    import train_lane_g_r264_execution_world_v1 as wm
except Exception:
    from tools import train_lane_g_r264_execution_world_v1 as wm

LABELS=('anyFill3s','anyFill5s')

def clip(p):return np.clip(np.asarray(p,float),1e-6,1-1e-6)
def logit(p):
    p=clip(p);return np.log(p/(1-p)).reshape(-1,1)
def auc(y,p):return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def ece(y,p,bins=10):
    y=np.asarray(y,int);p=np.asarray(p,float);edges=np.linspace(0,1,bins+1);z=0.0;details=[]
    for i in range(bins):
        mask=(p>=edges[i])&(p<(edges[i+1]) if i<bins-1 else p<=edges[i+1])
        n=int(mask.sum())
        if not n:continue
        conf=float(p[mask].mean());obs=float(y[mask].mean());w=n/len(y);z+=w*abs(conf-obs);details.append({'lo':float(edges[i]),'hi':float(edges[i+1]),'n':n,'meanPred':conf,'observed':obs})
    return float(z),details

def fit_base(rows,label,seed):
    x=wm.X(rows,True);y=np.asarray([int(r[label]) for r in rows],int)
    m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=15,l2_regularization=2.0,random_state=seed).fit(x,y)
    return m

def metrics(y,p):
    ec,bins=ece(y,p)
    return {'n':len(y),'positiveSupport':int(np.sum(y)),'baseRate':float(np.mean(y)),'auc':auc(y,p),'brier':float(brier_score_loss(y,p)),'ece10':ec,'bins':bins}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--training-rows',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    rows=[json.loads(x) for x in Path(a.training_rows).read_text(encoding='utf-8').splitlines() if x.strip()]
    mid_order=sorted({(int(r['windowEndMs']),int(r['marketId'])) for r in rows});mids=[m for _,m in mid_order]
    if len(mids)!=100:raise RuntimeError(f'expected 100 markets, got {len(mids)}')
    train=set(mids[:50]);cal=set(mids[50:70]);test=set(mids[70:])
    tr=[r for r in rows if int(r['marketId']) in train];ca=[r for r in rows if int(r['marketId']) in cal];te=[r for r in rows if int(r['marketId']) in test]
    report={'version':'LANE_G_R264_FILL_HAZARD_CALIBRATION_V1_20260907','researchOnly':True,'runtimeAuthority':False,'split':{'trainMarkets':50,'calibrationMarkets':20,'testMarkets':30,'trainRows':len(tr),'calibrationRows':len(ca),'testRows':len(te)},'labels':{}}
    artifacts={}
    for j,label in enumerate(LABELS):
        base=fit_base(tr,label,900+j);xcal=wm.X(ca,True);xtest=wm.X(te,True);ycal=np.asarray([int(r[label]) for r in ca]);ytest=np.asarray([int(r[label]) for r in te]);pcal=base.predict_proba(xcal)[:,1];ptest=base.predict_proba(xtest)[:,1]
        platt=LogisticRegression(C=1.0,max_iter=1000,random_state=1900+j).fit(logit(pcal),ycal);p_platt_cal=platt.predict_proba(logit(pcal))[:,1];p_platt_test=platt.predict_proba(logit(ptest))[:,1]
        iso=IsotonicRegression(out_of_bounds='clip').fit(pcal,ycal);p_iso_cal=np.asarray(iso.predict(pcal),float);p_iso_test=np.asarray(iso.predict(ptest),float)
        cal_scores={'RAW':metrics(ycal,pcal),'PLATT':metrics(ycal,p_platt_cal),'ISOTONIC':metrics(ycal,p_iso_cal)}
        # Selection is calibration-only; test is never consulted.
        chosen=min(cal_scores,key=lambda k:cal_scores[k]['brier'])
        test_probs={'RAW':ptest,'PLATT':p_platt_test,'ISOTONIC':p_iso_test};test_scores={k:metrics(ytest,v) for k,v in test_probs.items()}
        report['labels'][label]={'calibration':cal_scores,'chosenByCalibrationBrier':chosen,'test':test_scores,'testChosen':test_scores[chosen],'testBrierImprovementVsRaw':float(test_scores['RAW']['brier']-test_scores[chosen]['brier']),'testECEImprovementVsRaw':float(test_scores['RAW']['ece10']-test_scores[chosen]['ece10']),'testAucDeltaVsRaw':None if test_scores['RAW']['auc'] is None else float(test_scores[chosen]['auc']-test_scores['RAW']['auc'])}
        artifacts[label]={'base50':base,'platt':platt,'isotonic':iso,'chosen':chosen}
        print(json.dumps({'label':label,'chosen':chosen,'calBrier':{k:v['brier'] for k,v in cal_scores.items()},'testBrier':{k:v['brier'] for k,v in test_scores.items()},'testECE':{k:v['ece10'] for k,v in test_scores.items()}},ensure_ascii=False),flush=True)
    report['gates']={'bothChosenImproveTestBrier':all(v['testBrierImprovementVsRaw']>0 for v in report['labels'].values()),'bothChosenImproveTestECE':all(v['testECEImprovementVsRaw']>0 for v in report['labels'].values()),'aucNotMateriallyWorse':all((v['testAucDeltaVsRaw'] or 0.0)>=-0.005 for v in report['labels'].values())}
    report['calibrationPass']=all(report['gates'].values())
    report['boundary']=['chronological 50 train / 20 calibration / 30 final test','calibrator choice uses calibration 20 only','final 30 never used for fitting or choice','fill hazard only; no overflow policy authority','consumed H100 only','no fresh/no winner/no 8781']
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');joblib.dump({'version':report['version'],'split':report['split'],'artifacts':artifacts},op.parent/'calibration_models.joblib');print(json.dumps({'ok':True,'calibrationPass':report['calibrationPass'],'gates':report['gates']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
