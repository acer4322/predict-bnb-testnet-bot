from __future__ import annotations

import json, math
from collections import Counter
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import roc_auc_score, balanced_accuracy_score, recall_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
TEACH=P/'r4_r3_repair_counterfactual_teacher_v1_consolidated_20260830.json'
PATH_FILES=[
 ROOT/'data/research/lan_worker_returns/r4-repair-pathstate40-b1-v1/pathstate.json',
 ROOT/'data/research/lan_worker_returns/r4-repair-pathstate40-b2-v1/pathstate.json',
 ROOT/'data/research/lan_worker_returns/r4-repair-pathstate40-b3-v2/pathstate.json',
 ROOT/'data/research/lan_worker_returns/r4-repair-pathstate40-b4-v2/pathstate.json',
]
OUT=P/'r4_repair_exact_pathstate_ab_v1.json'

BASE_FEATURES=[
 'riskAgeS','activeMakerOrders','bookAgeS','makerAbsNet','makerCoverage','preFloor','preUpside','makerPairEdge',
 'pRepairMaker','pDominantMaker','pResidualWake','secondsLeft','directionInteraction','predictPairAskEdge'
]
PATH_FEATURES=[
 'repairSideActiveCount','dominantSideActiveCount','repairRemainingUnits','dominantRemainingUnits',
 'ageSkewS','quoteOffsetGapTicks','logDepletionGap'
]


def fnum(v):
    try:
        x=float(v); return x if math.isfinite(x) else math.nan
    except Exception:return math.nan


def load_teacher():
    obj=json.loads(TEACH.read_text(encoding='utf-8'))
    return {int(r['marketId']):r for r in obj.get('rows',[]) if isinstance(r,dict) and r.get('marketId') is not None}


def load_path():
    out={}
    for fp in PATH_FILES:
        obj=json.loads(fp.read_text(encoding='utf-8'))
        for r in obj.get('rows',[]):
            if isinstance(r,dict) and r.get('exactBranchApplied') is True and isinstance(r.get('pathState'),dict):
                out[int(r['marketId'])]=r
    return out


def base_values(r):
    c=r.get('candidate') or {}; p=c.get('portfolio') or {}; m=c.get('models') or {}; u=c.get('public') or {}
    maker_net=fnum(p.get('maker_net')); pu=fnum(m.get('pMakerUp')); pd=fnum(m.get('pMakerDown'))
    if math.isfinite(maker_net) and maker_net>0: prepair,pdom=pd,pu
    elif math.isfinite(maker_net) and maker_net<0: prepair,pdom=pu,pd
    else:
        prepair=max(pu,pd) if math.isfinite(pu) and math.isfinite(pd) else math.nan
        pdom=min(pu,pd) if math.isfinite(pu) and math.isfinite(pd) else math.nan
    direction=fnum(u.get('directionScore')); ua=fnum(u.get('predictUpAsk')); da=fnum(u.get('predictDownAsk'))
    pairask=1-ua-da if math.isfinite(ua) and math.isfinite(da) else math.nan
    return {
      'riskAgeS':fnum(c.get('riskAgeMs'))/1000.0,
      'activeMakerOrders':fnum(c.get('activeMakerOrders')),
      'bookAgeS':fnum(c.get('bookAgeMs'))/1000.0,
      'makerAbsNet':fnum(p.get('maker_abs_net')),
      'makerCoverage':fnum(p.get('maker_paired_coverage')),
      'preFloor':fnum(p.get('worst_case_floor')),
      'preUpside':fnum(p.get('best_case_pnl')),
      'makerPairEdge':fnum(p.get('maker_avg_pair_edge')),
      'pRepairMaker':prepair,'pDominantMaker':pdom,'pResidualWake':fnum(m.get('pResidualWake')),
      'secondsLeft':fnum(u.get('secondsLeft')),
      'directionInteraction':maker_net*direction/18.0 if math.isfinite(maker_net) and math.isfinite(direction) else math.nan,
      'predictPairAskEdge':pairask,
    }


def enriched_values(trow,prow,mode):
    v=base_values(trow)
    s=(prow.get('pathState') or {}).get('summary') or {}
    if mode in {'EXACT_STATE','EXACT_STATE_PATH'}:
        v['activeMakerOrders']=fnum(s.get('activeOrderCount'))
    if mode=='EXACT_STATE_PATH':
        ra=fnum(s.get('repairSideActiveCount')); da=fnum(s.get('dominantSideActiveCount'))
        rr=fnum(s.get('repairSideRemainingQty')); dr=fnum(s.get('dominantSideRemainingQty'))
        rage=fnum(s.get('repairMeanOrderAgeMs')); dage=fnum(s.get('dominantMeanOrderAgeMs'))
        roff=fnum(s.get('repairBestQuoteOffsetTicks')); doff=fnum(s.get('dominantBestQuoteOffsetTicks'))
        rdep=fnum(s.get('repairMeanDepletionRatio')); ddep=fnum(s.get('dominantMeanDepletionRatio'))
        v.update({
          'repairSideActiveCount':ra,'dominantSideActiveCount':da,
          'repairRemainingUnits':rr/18.0 if math.isfinite(rr) else math.nan,
          'dominantRemainingUnits':dr/18.0 if math.isfinite(dr) else math.nan,
          'ageSkewS':(rage-dage)/1000.0 if math.isfinite(rage) and math.isfinite(dage) else math.nan,
          'quoteOffsetGapTicks':roff-doff if math.isfinite(roff) and math.isfinite(doff) else math.nan,
          'logDepletionGap':math.log1p(max(0,rdep))-math.log1p(max(0,ddep)) if math.isfinite(rdep) and math.isfinite(ddep) else math.nan,
        })
    return v


def impute(train,test):
    med=np.nanmedian(train,axis=0); med=np.where(np.isfinite(med),med,0.0)
    return np.where(np.isfinite(train),train,med),np.where(np.isfinite(test),test,med)


def model_factory(name):
    if name=='LOGISTIC_BALANCED':
        return make_pipeline(StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260830))
    if name=='TREE_D2_LEAF4':
        return DecisionTreeClassifier(max_depth=2,min_samples_leaf=4,class_weight='balanced',random_state=20260830)
    raise KeyError(name)


def loo_predict(X,y,classes,model_name):
    probs=np.full(len(classes),np.nan)
    for i in range(len(classes)):
        train_idx=[j for j in range(len(classes)) if j!=i and classes[j] in {'PARETO_BENEFICIAL','PARETO_HARMFUL'}]
        if classes[i] not in {'PARETO_BENEFICIAL','PARETO_HARMFUL'}:
            train_idx=[j for j in range(len(classes)) if classes[j] in {'PARETO_BENEFICIAL','PARETO_HARMFUL'}]
        Xt=X[train_idx]; yt=np.asarray([1 if classes[j]=='PARETO_BENEFICIAL' else 0 for j in train_idx],int)
        tr,te=impute(Xt,X[i:i+1]); model=model_factory(model_name); model.fit(tr,yt)
        probs[i]=float(model.predict_proba(te)[0,1])
    return probs


def metrics(probs,classes):
    bh=np.asarray([c in {'PARETO_BENEFICIAL','PARETO_HARMFUL'} for c in classes]); yy=np.asarray([1 if c=='PARETO_BENEFICIAL' else 0 for c in classes],int)
    pred=(probs>=.5).astype(int)
    app=probs>=.65; veto=probs<=.35; other=~bh
    beneficial=np.asarray([c=='PARETO_BENEFICIAL' for c in classes]); harmful=np.asarray([c=='PARETO_HARMFUL' for c in classes])
    def rate(n,d):return float(n/d) if d else math.nan
    return {
      'bhN':int(bh.sum()),'auc':float(roc_auc_score(yy[bh],probs[bh])),
      'balancedAccuracyAt050':float(balanced_accuracy_score(yy[bh],pred[bh])),
      'beneficialRecallAt050':float(recall_score(yy[bh],pred[bh],pos_label=1)),
      'harmfulRecallAt050':float(recall_score(yy[bh],pred[bh],pos_label=0)),
      'decisionCoverage':float(np.mean(app|veto)),'abstainRate':float(np.mean(~(app|veto))),
      'beneficialOpportunityRetention':rate(int(np.sum(app&beneficial)),int(beneficial.sum())),
      'harmfulVetoRecall':rate(int(np.sum(veto&harmful)),int(harmful.sum())),
      'approvePrecisionBeneficial':rate(int(np.sum(app&beneficial)),int(app.sum())),
      'approveHarmfulRate':rate(int(np.sum(app&harmful)),int(app.sum())),
      'vetoPrecisionHarmful':rate(int(np.sum(veto&harmful)),int(veto.sum())),
      'vetoBeneficialRate':rate(int(np.sum(veto&beneficial)),int(veto.sum())),
      'harmfulFalseApproveCount':int(np.sum(app&harmful)),'beneficialFalseVetoCount':int(np.sum(veto&beneficial)),
      'otherExtremeDecisionRate':rate(int(np.sum((app|veto)&other)),int(other.sum())),
    }


def main():
    teacher=load_teacher(); path=load_path(); mids=sorted(set(teacher)&set(path))
    rows=[]
    for mid in mids:
        c=str(teacher[mid].get('branchClass'))
        if c not in {'PARETO_BENEFICIAL','PARETO_HARMFUL','TRADEOFF','NO_EFFECT'}:continue
        rows.append((mid,c,teacher[mid],path[mid]))
    classes=[x[1] for x in rows]
    result={'version':'R4_REPAIR_EXACT_PATHSTATE_AB_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,
            'exactMarketCount':len(rows),'classCounts':dict(Counter(classes)),
            'validation':'same exact-seam market set for all representations; leave-one-market-out for B/H rows; OTHER rows predicted from all B/H; thresholds fixed 0.65/0.35',
            'representations':{},'semanticFinding':'OLD_STATE activeMakerOrders is post-decision contaminated for at least some markets; EXACT_STATE uses pre-decision seam activeOrderCount.'}
    for mode in ['OLD_STATE','EXACT_STATE','EXACT_STATE_PATH']:
        feats=BASE_FEATURES+(PATH_FEATURES if mode=='EXACT_STATE_PATH' else [])
        raw=[enriched_values(t,p,mode) for _,_,t,p in rows]
        X=np.asarray([[r.get(f,math.nan) for f in feats] for r in raw],float)
        rr={'featureNames':feats,'models':{}}
        for model in ['LOGISTIC_BALANCED','TREE_D2_LEAF4']:
            pr=loo_predict(X,None,classes,model)
            rr['models'][model]=metrics(pr,classes)
        result['representations'][mode]=rr
    # exact active-order audit after semantic correction
    exact0=[rows[i][1] for i in range(len(rows)) if fnum((rows[i][3].get('pathState') or {}).get('summary',{}).get('activeOrderCount'))==0]
    result['exactActive0Audit']={'n':len(exact0),'classCounts':dict(Counter(exact0))}
    OUT.write_text(json.dumps(result,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'exactMarketCount':result['exactMarketCount'],'classCounts':result['classCounts'],'exactActive0Audit':result['exactActive0Audit'],
      'models':{m:{m2:result['representations'][m]['models'][m2] for m2 in result['representations'][m]['models']} for m in result['representations']}},indent=2,allow_nan=True))

if __name__=='__main__':main()
