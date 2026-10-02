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
OUT=P/'r4_repair_participation_collapse_risk_head_probe_v1.json'

def loo(rows, features):
    X=np.asarray([[r['v'].get(f,math.nan) for f in features] for r in rows],float)
    y=np.asarray([1 if r['target']=='BENEFICIAL_BUT_PARTICIPATION_COLLAPSE' else 0 for r in rows],int)
    p=np.zeros(len(rows))
    for i in range(len(rows)):
        mask=np.arange(len(rows))!=i
        tr,te=impute(X[mask],X[i:i+1])
        m=model_factory('LOGISTIC_BALANCED'); m.fit(tr,y[mask]); p[i]=m.predict_proba(te)[0,1]
    return y,p

def summarize(y,p):
    out={'auc':float(roc_auc_score(y,p))}
    for t in (0.35,0.5,0.65):
        pred=(p>=t).astype(int)
        out[f'balancedAccuracyAt{int(t*100):02d}']=float(balanced_accuracy_score(y,pred))
        out[f'collapseRecallAt{int(t*100):02d}']=float(np.mean(pred[y==1]==1))
        out[f'retainedSpecificityAt{int(t*100):02d}']=float(np.mean(pred[y==0]==0))
    return out

def main():
    teacher=load_teacher(); path=load_path(); overlay=json.loads(OVER.read_text(encoding='utf-8'))
    omap={int(r['marketId']):r for r in overlay['rows']}
    rows=[]
    for mid in sorted(set(teacher)&set(path)&set(omap)):
        tc=omap[mid]['participationAwareTeacherClass']
        if tc not in {'BENEFICIAL_PARTICIPATION_RETAINED','BENEFICIAL_BUT_PARTICIPATION_COLLAPSE'}: continue
        vals=enriched_values(teacher[mid],path[mid],'EXACT_STATE_PATH')
        rows.append({'marketId':mid,'target':tc,'retention':omap[mid].get('makerParticipationRetention'),'floorDelta':omap[mid].get('floorDelta'),'v':vals})
    reps={
      'EXACT_STATE':BASE_FEATURES,
      'PATH_ONLY_COMPACT':['activeMakerOrders']+PATH_FEATURES,
      'EXACT_STATE_PATH':BASE_FEATURES+PATH_FEATURES,
      'EXACT_STATE_PLUS_QUOTE':BASE_FEATURES+['quoteOffsetGapTicks'],
      'PARTICIPATION_COMPACT':['activeMakerOrders','repairSideActiveCount','dominantSideActiveCount','repairRemainingUnits','dominantRemainingUnits','quoteOffsetGapTicks','logDepletionGap','secondsLeft']
    }
    results={}; scores={}
    for name,features in reps.items():
        y,p=loo(rows,features)
        results[name]={'features':features,**summarize(y,p)}
        scores[name]=[{'marketId':r['marketId'],'target':r['target'],'pCollapse':float(s),'makerParticipationRetention':r['retention'],'floorDelta':r['floorDelta']} for r,s in zip(rows,p)]
    rep={'version':'R4_REPAIR_PARTICIPATION_COLLAPSE_RISK_HEAD_PROBE_V1','date':'2026-08-30','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'strictPastFeatureBoundary':True,'counterfactualOutcomeUsedOnlyAsTeacher':True,
      'question':'Conditional on raw counterfactual Repair benefit, can strict-past features distinguish participation-retained benefit from benefit obtained through Passive Maker participation collapse?',
      'n':len(rows),'classCounts':dict(Counter(r['target'] for r in rows)),'representations':results,'looScores':scores,
      'interpretationBoundary':'Tiny consumed-development diagnostic only. Four collapse positives are insufficient for promotion or runtime threshold selection. This head is factorized from economic-value prediction and grants no action authority.',
      'guards':['Passive Maker/Formation participation mandatory','Protection deterministic unchanged','no learned action authority','<=180s no new exposure authority','live R3/R3.1/8781 untouched','fresh cohort not consumed']}
    OUT.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'n':rep['n'],'classCounts':rep['classCounts'],'representations':rep['representations']},indent=2,allow_nan=True))
if __name__=='__main__': main()
