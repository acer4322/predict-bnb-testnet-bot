from __future__ import annotations
import json, math, sys
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.evaluate_r4_repair_exact_pathstate_ab_v1 import load_teacher,load_path,base_values,impute,model_factory
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OVER=P/'r4_repair_participation_aware_teacher_overlay_v1.json'
OUT=P/'r4_repair_passive_continuity_orderstate_probe_v1.json'
BASE=['riskAgeS','activeMakerOrders','bookAgeS','makerAbsNet','makerCoverage','preFloor','preUpside','makerPairEdge','pRepairMaker','pDominantMaker','pResidualWake','secondsLeft','directionInteraction','predictPairAskEdge']

def f(v):
    try:
        x=float(v); return x if math.isfinite(x) else math.nan
    except Exception:return math.nan

def agg_orderstate(prow):
    ps=prow.get('pathState') or {}; s=ps.get('summary') or {}; orders=[o for o in ps.get('orders',[]) if isinstance(o,dict)]
    def arr(k):
        a=np.asarray([f(o.get(k)) for o in orders],float); return a[np.isfinite(a)]
    age=arr('orderAgeMs'); off=arr('quoteOffsetTicks'); dep=arr('publicDepletionRatio'); depth=arr('initialDepth'); spr=arr('currentSpreadTicks'); rem=arr('remainingRatio'); part=arr('partialFillRatio')
    occ=np.asarray([1.0 if o.get('occupiedBefore') is True else 0.0 for o in orders],float) if orders else np.asarray([],float)
    anydep=np.asarray([1.0 if o.get('publicAnyDepletion') is True else 0.0 for o in orders],float) if orders else np.asarray([],float)
    return {
      'activeMakerOrders':f(s.get('activeOrderCount')),
      'occupiedBeforeShare':float(np.mean(occ)) if len(occ) else math.nan,
      'anyDepletionShare':float(np.mean(anydep)) if len(anydep) else math.nan,
      'meanOrderAgeS':float(np.mean(age)/1000.0) if len(age) else math.nan,
      'maxOrderAgeS':float(np.max(age)/1000.0) if len(age) else math.nan,
      'minQuoteOffsetTicks':float(np.min(off)) if len(off) else math.nan,
      'meanQuoteOffsetTicks':float(np.mean(off)) if len(off) else math.nan,
      'quoteOffsetDispersion':float(np.std(off)) if len(off)>1 else 0.0 if len(off)==1 else math.nan,
      'meanLog1pInitialDepth':float(np.mean(np.log1p(np.maximum(depth,0)))) if len(depth) else math.nan,
      'meanLog1pDepletion':float(np.mean(np.log1p(np.maximum(dep,0)))) if len(dep) else math.nan,
      'meanSpreadTicks':float(np.mean(spr)) if len(spr) else math.nan,
      'meanRemainingRatio':float(np.mean(rem)) if len(rem) else math.nan,
      'meanPartialFillRatio':float(np.mean(part)) if len(part) else math.nan,
      'repairSideShare': (f(s.get('repairSideActiveCount'))/f(s.get('activeOrderCount'))) if f(s.get('activeOrderCount'))>0 else 0.0,
      'dominantSideShare': (f(s.get('dominantSideActiveCount'))/f(s.get('activeOrderCount'))) if f(s.get('activeOrderCount'))>0 else 0.0,
    }

def loo(rows,features):
    X=np.asarray([[r['v'].get(k,math.nan) for k in features] for r in rows],float); y=np.asarray([r['y'] for r in rows],int); p=np.zeros(len(rows))
    for i in range(len(rows)):
        mask=np.arange(len(rows))!=i; tr,te=impute(X[mask],X[i:i+1]); m=model_factory('LOGISTIC_BALANCED'); m.fit(tr,y[mask]); p[i]=m.predict_proba(te)[0,1]
    pred=(p>=.5).astype(int)
    # positive=collapse, so specificity means retained-beneficial kept by veto head
    return p,{'auc':float(roc_auc_score(y,p)),'balancedAccuracyAt050':float(balanced_accuracy_score(y,pred)),'collapseRecallAt050':float(np.mean(pred[y==1]==1)),'retainedBeneficialSpecificityAt050':float(np.mean(pred[y==0]==0)),'retainedBeneficialFalseVetoRateAt050':float(np.mean(pred[y==0]==1))}

def main():
    teacher=load_teacher(); path=load_path(); over=json.loads(OVER.read_text(encoding='utf-8')); om={int(r['marketId']):r for r in over['rows']}
    rows=[]
    for mid in sorted(set(teacher)&set(path)&set(om)):
        cls=om[mid].get('participationAwareTeacherClass')
        if cls not in {'BENEFICIAL_PARTICIPATION_RETAINED','BENEFICIAL_BUT_PARTICIPATION_COLLAPSE'}: continue
        v=base_values(teacher[mid]); v.update(agg_orderstate(path[mid])); rows.append({'marketId':mid,'class':cls,'y':1 if cls.endswith('COLLAPSE') else 0,'retention':om[mid].get('makerParticipationRetention'),'v':v})
    CONT=['occupiedBeforeShare','anyDepletionShare','meanOrderAgeS','maxOrderAgeS','minQuoteOffsetTicks','meanQuoteOffsetTicks','quoteOffsetDispersion','meanLog1pInitialDepth','meanLog1pDepletion','meanSpreadTicks','meanRemainingRatio','meanPartialFillRatio','repairSideShare','dominantSideShare']
    CORE_CONT=['occupiedBeforeShare','anyDepletionShare','meanOrderAgeS','minQuoteOffsetTicks','meanLog1pInitialDepth','meanLog1pDepletion','meanSpreadTicks','repairSideShare','dominantSideShare']
    reps={'EXACT_STATE':BASE,'ORDER_CONTINUITY_ONLY':CORE_CONT,'EXACT_STATE_PLUS_CORE_CONTINUITY':BASE+CORE_CONT,'EXACT_STATE_PLUS_FULL_ORDERSTATE':BASE+CONT}
    out={'version':'R4_REPAIR_PASSIVE_CONTINUITY_ORDERSTATE_PROBE_V1','date':'2026-08-30','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'freshCohortConsumed':False,'strictPastFeatureBoundary':True,'counterfactualOutcomeUsedOnlyAsTeacher':True,'question':'Can pre-action order-level passive-continuity state identify raw-beneficial Repair cases whose apparent economic improvement is accompanied by Passive Maker participation collapse, without excessive false veto of participation-retained beneficial cases?','n':len(rows),'classCounts':dict(Counter(r['class'] for r in rows)),'validation':'market-level leave-one-out; logistic balanced; fixed 0.50 diagnostic threshold; fixed representations, no threshold sweep','representations':{},'rows':[],'guards':['Passive Maker/Formation participation mandatory','Protection deterministic unchanged','no learned action authority','<=180s no new exposure authority','live R3/R3.1/8781 untouched','no dream fill','fresh cohort not consumed']}
    preds={}
    for name,fs in reps.items():
        p,m=loo(rows,fs); preds[name]=p; out['representations'][name]={'features':fs,**m}
    for i,r in enumerate(rows):
        out['rows'].append({'marketId':r['marketId'],'class':r['class'],'makerParticipationRetention':r['retention'],**{f'pCollapse_{k}':float(preds[k][i]) for k in reps}})
    out['interpretationBoundary']='Consumed-development representation probe only. Better collapse discrimination may justify feature engineering, but cannot authorize REPAIR_NOW/WAIT or count as fresh promotion evidence.'
    OUT.write_text(json.dumps(out,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'n':out['n'],'classCounts':out['classCounts'],'representations':out['representations']},indent=2,allow_nan=True))
if __name__=='__main__': main()
