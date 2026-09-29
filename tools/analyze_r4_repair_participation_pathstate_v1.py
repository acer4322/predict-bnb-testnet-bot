from __future__ import annotations
import json, math, sys
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.evaluate_r4_repair_exact_pathstate_ab_v1 import load_teacher,load_path,enriched_values,impute,model_factory,BASE_FEATURES,PATH_FEATURES
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OVER=P/'r4_repair_participation_aware_teacher_overlay_v1.json'
OUT=P/'r4_repair_participation_pathstate_gate_probe_v1.json'

def loo(rows, features):
    X=np.asarray([[r['v'].get(f,math.nan) for f in features] for r in rows],float)
    y=np.asarray([1 if r['target']=='BENEFICIAL_PARTICIPATION_RETAINED' else 0 for r in rows],int)
    pr=np.zeros(len(rows))
    for i in range(len(rows)):
        mask=np.arange(len(rows))!=i
        tr,te=impute(X[mask],X[i:i+1])
        m=model_factory('LOGISTIC_BALANCED'); m.fit(tr,y[mask]); pr[i]=m.predict_proba(te)[0,1]
    return X,y,pr

def metrics(y,p):
    pred=(p>=.5).astype(int)
    return {'auc':float(roc_auc_score(y,p)),'balancedAccuracyAt050':float(balanced_accuracy_score(y,pred)),
            'retainedBeneficialRecallAt050':float(np.mean(pred[y==1]==1)),
            'harmfulRecallAt050':float(np.mean(pred[y==0]==0))}

def fit_score(train_rows, score_rows, features):
    X=np.asarray([[r['v'].get(f,math.nan) for f in features] for r in train_rows],float)
    y=np.asarray([1 if r['target']=='BENEFICIAL_PARTICIPATION_RETAINED' else 0 for r in train_rows],int)
    Z=np.asarray([[r['v'].get(f,math.nan) for f in features] for r in score_rows],float)
    X2,Z2=impute(X,Z)
    m=model_factory('LOGISTIC_BALANCED'); m.fit(X2,y)
    return m.predict_proba(Z2)[:,1]

def main():
    teacher=load_teacher(); path=load_path(); overlay=json.loads(OVER.read_text(encoding='utf-8'))
    omap={int(r['marketId']):r for r in overlay['rows']}
    joined=[]
    for mid in sorted(set(teacher)&set(path)&set(omap)):
        tc=omap[mid]['participationAwareTeacherClass']
        vals=enriched_values(teacher[mid],path[mid],'EXACT_STATE_PATH')
        joined.append({'marketId':mid,'target':tc,'retention':omap[mid].get('makerParticipationRetention'),'floorDelta':omap[mid].get('floorDelta'),'v':vals})
    binary=[r for r in joined if r['target'] in {'BENEFICIAL_PARTICIPATION_RETAINED','PARETO_HARMFUL'}]
    collapse=[r for r in joined if r['target']=='BENEFICIAL_BUT_PARTICIPATION_COLLAPSE']
    reps={'EXACT_STATE':BASE_FEATURES,'PATH_ONLY_COMPACT':['activeMakerOrders']+PATH_FEATURES,'EXACT_STATE_PATH':BASE_FEATURES+PATH_FEATURES,
          'EXACT_STATE_PLUS_QUOTE':BASE_FEATURES+['quoteOffsetGapTicks']}
    result={}
    collapse_scores={}
    for name,features in reps.items():
        _,y,p=loo(binary,features); result[name]={'features':features,**metrics(y,p)}
        if collapse:
            cp=fit_score(binary,collapse,features)
            collapse_scores[name]=[{'marketId':r['marketId'],'pRetainedBeneficial':float(s),'makerParticipationRetention':r['retention'],'floorDelta':r['floorDelta']} for r,s in zip(collapse,cp)]
            result[name]['collapseMeanPredRetainedBeneficial']=float(np.mean(cp))
            result[name]['collapseHighApproveRateP065']=float(np.mean(cp>=.65))
    rep={'version':'R4_REPAIR_PARTICIPATION_PATHSTATE_GATE_PROBE_V1','date':'2026-08-30','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,
         'strictPastFeatureBoundary':True,'counterfactualOutcomeUsedOnlyAsTeacher':True,
         'joinedExactPathMarkets':len(joined),'joinedClassCounts':dict(Counter(r['target'] for r in joined)),
         'binaryEvaluationCount':len(binary),'binaryClassCounts':dict(Counter(r['target'] for r in binary)),
         'representations':result,'participationCollapsePosthocScores':collapse_scores,
         'interpretationBoundary':'Development-only causal-teacher representation probe. Binary LOO asks whether strict-past state can separate participation-retained beneficial Repair from harmful Repair. Collapse rows are scored posthoc only to audit whether the representation would mistake participation collapse for valid success. No runtime threshold/action authority/promotion is granted.',
         'guards':['Passive Maker/Formation participation mandatory','Protection deterministic unchanged','no learned action authority','<=180s no new exposure authority','live R3/R3.1/8781 untouched','fresh cohort not consumed']}
    OUT.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'joinedExactPathMarkets':rep['joinedExactPathMarkets'],'joinedClassCounts':rep['joinedClassCounts'],'binaryEvaluationCount':rep['binaryEvaluationCount'],'representations':rep['representations'],'participationCollapsePosthocScores':rep['participationCollapsePosthocScores']},indent=2,allow_nan=True))
if __name__=='__main__': main()
