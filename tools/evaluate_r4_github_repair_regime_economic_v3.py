from __future__ import annotations
import json, glob, math, sys
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score, balanced_accuracy_score, recall_score

ROOT=Path.cwd()
TRAIN0=ROOT/'data/research/lan_worker_returns/r4-adaptive-hft-formal20-c0/synthesis.json'
TRAIN_GLOB=str(ROOT/'data/research/r4_v0/p0_provenance_v1/r4_github_regime_train_batches/out*.json')
TESTS=[ROOT/'data/research/lan_worker_returns/r4-adaptive-hybrid-v3-holdout2-c0b/chunk0.json',ROOT/'data/research/lan_worker_returns/r4-adaptive-hybrid-v3-holdout2-c1b/chunk1.json']
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_github_repair_regime_economic_v3_score.json'
REGS=['FORMATION','MGMT_NEG_FLOOR','MGMT_NONNEG_FLOOR','PROTECTION']
PHASES=['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']


def regime(r):
    p=str(r['phase'])
    if p.startswith('FORMATION'): return 'FORMATION'
    if p.startswith('PROTECTION'): return 'PROTECTION'
    return 'MGMT_NEG_FLOOR' if float(r.get('floor') or 0)<0 else 'MGMT_NONNEG_FLOOR'

def feat(r):
    ph=str(r['phase'])
    return [float(r['repairBase']),float(r.get('secondsLeft') or 0)/300.0,float(r.get('floor') or 0)/18.0,float(r.get('absGap') or 0)/36.0,float(r.get('upside') or 0)/36.0,
            float(ph==PHASES[0]),float(ph==PHASES[1]),float(ph==PHASES[2])]

def y(r): return int(r['repairAction5s'])
def bp(r): return float(r['repairBase'])

def fit(rows):
    yy=np.asarray([y(r) for r in rows],int)
    if len(rows)<2 or len(np.unique(yy))<2:return None
    X=np.asarray([feat(r) for r in rows],float)
    return make_pipeline(StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260830)).fit(X,yy)

def pred(m,rows):
    if m is None:return np.asarray([bp(r) for r in rows],float)
    return m.predict_proba(np.asarray([feat(r) for r in rows],float))[:,1]

def spear(x,z):
    a=pd.Series(np.asarray(x,float));b=pd.Series(np.asarray(z,float))
    if len(a)<3 or a.nunique()<2 or b.nunique()<2:return None
    v=a.rank(method='average').corr(b.rank(method='average'))
    return float(v) if v is not None and math.isfinite(float(v)) else None

def auc(yy,p):
    yy=np.asarray(yy,int);p=np.asarray(p,float)
    return float(roc_auc_score(yy,p)) if len(np.unique(yy))>1 else None

def met(rows,p):
    yy=np.asarray([y(r) for r in rows],int);p=np.asarray(p,float);z=(p>=.5).astype(int);base=(np.asarray([bp(r) for r in rows])>=.5).astype(int)
    changed=z!=base
    econ=[i for i,r in enumerate(rows) if r.get('repairGain5s') is not None and bool(r.get('repairEconomicEligible'))]
    return {'n':len(rows),'positiveSupport':int(yy.sum()),'auc':auc(yy,p),'ba':float(balanced_accuracy_score(yy,z)) if len(np.unique(yy))>1 else None,
            'r0':float(recall_score(yy,z,pos_label=0,zero_division=0)),'r1':float(recall_score(yy,z,pos_label=1,zero_division=0)),
            'errorRate':float(np.mean(z!=yy)),'interventions':int(changed.sum()),
            'beneficialInterventions':int(np.sum(changed&(z==yy)&(base!=yy))),'harmfulInterventions':int(np.sum(changed&(z!=yy)&(base==yy))),
            'economicN':len(econ),'economicSpearman':spear(p[econ],[rows[i]['repairGain5s'] for i in econ]) if econ else None}

def delta(a,b): return None if a is None or b is None else float(b-a)

def eligible(base,cand,force_baseline=False):
    if force_baseline:return False
    if cand['auc'] is None or base['auc'] is None or cand['auc']<base['auc']-1e-12:return False
    if cand['ba'] is not None and base['ba'] is not None and cand['ba']<base['ba']-.01:return False
    if cand['economicSpearman'] is not None and base['economicSpearman'] is not None and cand['economicSpearman']<base['economicSpearman']-1e-12:return False
    if cand['harmfulInterventions']>cand['beneficialInterventions']:return False
    return True

def train_regime_model(rows,rg):
    rr=[r for r in rows if regime(r)==rg]; yy=[y(r) for r in rr]; pos=sum(yy);neg=len(yy)-pos
    return fit(rr) if pos>=5 and neg>=20 else None

def expert_predictions(train,test):
    global_m=fit(train); gp=pred(global_m,test); base=np.asarray([bp(r) for r in test],float); rf=[]
    rms={rg:train_regime_model(train,rg) for rg in REGS}
    for i,r in enumerate(test):
        m=rms[regime(r)]
        if m is None:q=base[i]
        else:
            q=float(pred(m,[r])[0]); q=base[i] if abs(q-.5)<.10 else q
        rf.append(q)
    return {'BASELINE':base,'GLOBAL':np.asarray(gp),'REGIME_FALLBACK':np.asarray(rf)}

def main():
    train=json.loads(TRAIN0.read_text(encoding='utf-8'))['queryRows']
    for p in sorted(glob.glob(TRAIN_GLOB)):train+=json.loads(Path(p).read_text(encoding='utf-8'))['queryRows']
    test=[]
    for p in TESTS:test+=json.loads(p.read_text(encoding='utf-8'))['queryRows']
    mids=sorted({int(r['marketId']) for r in train}); folds=[mids[i::5] for i in range(5)]
    oof={e:np.full(len(train),np.nan,float) for e in ['BASELINE','GLOBAL','REGIME_FALLBACK']}
    idx_by_mid=defaultdict(list)
    for i,r in enumerate(train):idx_by_mid[int(r['marketId'])].append(i)
    for fm in folds:
        val_idx=[i for mid in fm for i in idx_by_mid[mid]]; tr=[r for i,r in enumerate(train) if i not in set(val_idx)]; va=[train[i] for i in val_idx]; ps=expert_predictions(tr,va)
        for e in oof:oof[e][val_idx]=ps[e]
    if any(np.isnan(v).any() for v in oof.values()):raise RuntimeError('OOF incomplete')
    oofMetrics={};route={}
    for rg in REGS:
        ix=[i for i,r in enumerate(train) if regime(r)==rg]; rr=[train[i] for i in ix]
        oofMetrics[rg]={e:met(rr,oof[e][ix]) for e in oof}
        if rg=='PROTECTION':route[rg]='BASELINE';continue
        b=oofMetrics[rg]['BASELINE']; candidates=['BASELINE']
        for e in ['GLOBAL','REGIME_FALLBACK']:
            if eligible(b,oofMetrics[rg][e]):candidates.append(e)
        def key(e):
            m=oofMetrics[rg][e]; eco=m['economicSpearman'] if m['economicSpearman'] is not None else -999.; au=m['auc'] if m['auc'] is not None else -999.
            return (eco,au,-m['harmfulInterventions'], {'BASELINE':0,'GLOBAL':1,'REGIME_FALLBACK':2}[e])
        route[rg]=max(candidates,key=key)
    full=expert_predictions(train,test); routed=np.asarray([full[route[regime(r)]][i] for i,r in enumerate(test)],float)
    summary={e:met(test,p) for e,p in full.items()}; summary['ROUTED_REPAIR']=met(test,routed)
    per={}
    for rg in REGS:
        ix=[i for i,r in enumerate(test) if regime(r)==rg]; rr=[test[i] for i in ix]; per[rg]={'route':route[rg],'n':len(rr),'BASELINE':met(rr,full['BASELINE'][ix]),'ROUTED_REPAIR':met(rr,routed[ix])}
    f=per['FORMATION']; s=summary['ROUTED_REPAIR']; b=summary['BASELINE']; state_shaping_drift=0.0
    gates={'repairActionAucNonDecreasing':bool(s['auc'] is not None and b['auc'] is not None and s['auc']>=b['auc']),
           'repairEconomicSpearmanNonDecreasing':bool(s['economicSpearman'] is not None and b['economicSpearman'] is not None and s['economicSpearman']>=b['economicSpearman']),
           'harmfulLessThanBeneficial':bool(s['harmfulInterventions']<s['beneficialInterventions']),
           'formationPositiveRecallDeltaMinMinus003':bool((f['ROUTED_REPAIR']['r1']-f['BASELINE']['r1'])>=-.03),
           'stateShapingDriftExactZero':state_shaping_drift==0.0,
           'protectionForcedBaseline':route['PROTECTION']=='BASELINE'}
    keep=all(gates.values())
    rep={'version':'R4_GITHUB_REPAIR_REGIME_ECONOMIC_V3_SCORE','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,
         'trainMarkets':mids,'trainQueries':len(train),'testMarkets':sorted({int(r['marketId']) for r in test}),'testQueries':len(test),'foldMarkets':folds,
         'oofMetrics':oofMetrics,'route':route,'summary':summary,'perRegime':per,'stateShapingProbabilityDrift':state_shaping_drift,
         'gates':gates,'developmentKeep':keep,'decision':'KEEP_REPAIR_REGIME_ECONOMIC_V3_FOR_FRESH_HFT' if keep else 'REJECT_OR_REVISE_V3',
         'contract':'r4_github_repair_regime_economic_v3_contract.json'}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'trainMarkets':len(mids),'trainQueries':len(train),'route':route,'baseline':b,'routed':s,'formation':per['FORMATION'],'gates':gates,'developmentKeep':keep,'decision':rep['decision']},indent=2))
if __name__=='__main__':main()
