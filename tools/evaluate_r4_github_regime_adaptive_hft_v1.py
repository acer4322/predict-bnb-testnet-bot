from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score, balanced_accuracy_score

ROOT=Path.cwd()
TRAIN=ROOT/'data/research/lan_worker_returns/r4-adaptive-hft-formal20-c0/synthesis.json'
TESTS=[ROOT/'data/research/lan_worker_returns/r4-adaptive-hybrid-v3-holdout2-c0b/chunk0.json',ROOT/'data/research/lan_worker_returns/r4-adaptive-hybrid-v3-holdout2-c1b/chunk1.json']
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_github_regime_adaptive_hft_v1_score.json'

PHASES=['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']

def regime(r):
    p=str(r['phase'])
    if p.startswith('FORMATION'): return 'FORMATION'
    if p.startswith('PROTECTION'): return 'PROTECTION'
    return 'MGMT_NEG_FLOOR' if float(r.get('floor') or 0)<0 else 'MGMT_NONNEG_FLOOR'

def features(r,cap):
    p=float(r['repairBase'] if cap=='repair' else r['addBase'])
    ph=str(r['phase'])
    return [p,float(r.get('secondsLeft') or 0)/300.0,float(r.get('floor') or 0)/18.0,float(r.get('absGap') or 0)/36.0,float(r.get('upside') or 0)/36.0,
            float(ph==PHASES[0]),float(ph==PHASES[1]),float(ph==PHASES[2])]

def label(r,cap): return int(r['repairAction5s'] if cap=='repair' else r['addAction5s'])
def basep(r,cap): return float(r['repairBase'] if cap=='repair' else r['addBase'])

def fit(rows,cap):
    X=np.asarray([features(r,cap) for r in rows],float); y=np.asarray([label(r,cap) for r in rows],int)
    if len(np.unique(y))<2:return None
    return make_pipeline(StandardScaler(),LogisticRegression(C=0.5,class_weight='balanced',max_iter=2000,random_state=20260830)).fit(X,y)

def pred(model,rows,cap):
    if model is None:return np.asarray([basep(r,cap) for r in rows],float)
    return model.predict_proba(np.asarray([features(r,cap) for r in rows],float))[:,1]

def auc(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float)
    return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None

def ba(y,p):
    y=np.asarray(y,int);z=(np.asarray(p,float)>=.5).astype(int)
    return float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None

def spearman(x,y):
    x=np.asarray(x,float);y=np.asarray(y,float)
    if len(x)<3 or len(np.unique(x))<2 or len(np.unique(y))<2:return None
    rx=np.argsort(np.argsort(x)).astype(float); ry=np.argsort(np.argsort(y)).astype(float)
    c=np.corrcoef(rx,ry)[0,1]
    return float(c) if math.isfinite(float(c)) else None

def metrics(rows,cap,p):
    y=np.asarray([label(r,cap) for r in rows],int); b=np.asarray([basep(r,cap) for r in rows],float)
    out={'n':len(rows),'positiveSupport':int(y.sum()),'auc':auc(y,p),'ba':ba(y,p),'errorRate':float(np.mean((p>=.5).astype(int)!=y))}
    zb=(b>=.5).astype(int); z=(p>=.5).astype(int); changed=z!=zb
    out['interventions']=int(changed.sum());out['beneficialInterventions']=int(np.sum(changed&(z==y)&(zb!=y)));out['harmfulInterventions']=int(np.sum(changed&(z!=y)&(zb==y)))
    if cap=='repair':
        e=[i for i,r in enumerate(rows) if r.get('repairGain5s') is not None and bool(r.get('repairEconomicEligible'))]
        out['economicN']=len(e);out['economicSpearman']=spearman(p[e],[rows[i]['repairGain5s'] for i in e]) if e else None
    else:
        e=[i for i,r in enumerate(rows) if r.get('safeUpside5s') is not None]
        out['safeUpsideN']=len(e);out['safeUpsidePositive']=int(sum(int(rows[i]['safeUpside5s']) for i in e))
        out['safeUpsideAuc']=auc([int(rows[i]['safeUpside5s']) for i in e],p[e]) if e else None
    return out

def main():
    train=json.loads(TRAIN.read_text(encoding='utf-8'))['queryRows']; test=[]
    for p in TESTS:test+=json.loads(p.read_text(encoding='utf-8'))['queryRows']
    caps=['repair','add']; regs=['FORMATION','MGMT_NEG_FLOOR','MGMT_NONNEG_FLOOR','PROTECTION']
    models={};support={}
    preds={v:{} for v in ['BASELINE','GLOBAL','REGIME','REGIME_FALLBACK']}
    for cap in caps:
        gm=fit(train,cap);models[(cap,'GLOBAL')]=gm
        for rg in regs:
            rr=[r for r in train if regime(r)==rg]; yy=[label(r,cap) for r in rr];pos=sum(yy);neg=len(yy)-pos
            support[(cap,rg)]={'n':len(rr),'pos':pos,'neg':neg}
            models[(cap,rg)]=fit(rr,cap) if pos>=5 and neg>=20 else None
        bp=np.asarray([basep(r,cap) for r in test],float); gp=pred(gm,test,cap); rp=[];rf=[]
        for i,r in enumerate(test):
            rg=regime(r); m=models[(cap,rg)]
            q=float(pred(m,[r],cap)[0]) if m is not None else float(bp[i])
            rp.append(q);rf.append(float(bp[i]) if (m is None or abs(q-.5)<.10) else q)
        preds['BASELINE'][cap]=bp;preds['GLOBAL'][cap]=gp;preds['REGIME'][cap]=np.asarray(rp);preds['REGIME_FALLBACK'][cap]=np.asarray(rf)
    summary={}
    for v in preds:
        summary[v]={cap:metrics(test,cap,preds[v][cap]) for cap in caps}
    perRegime={}
    wins=0
    for rg in regs:
        ix=[i for i,r in enumerate(test) if regime(r)==rg]; rr=[test[i] for i in ix]; perRegime[rg]={'n':len(rr)}
        for v in ['GLOBAL','REGIME','REGIME_FALLBACK']:
            er=[]
            for cap in caps:
                y=np.asarray([label(r,cap) for r in rr],int);p=preds[v][cap][ix];er.append(float(np.mean((p>=.5).astype(int)!=y)))
            perRegime[rg][v+'MeanActionError']=float(np.mean(er))
        delta=perRegime[rg]['GLOBALMeanActionError']-perRegime[rg]['REGIME_FALLBACKMeanActionError'];perRegime[rg]['errorImprovementFallbackVsGlobal']=delta
        if delta>0:wins+=1
    rep={'version':'R4_GITHUB_REGIME_ADAPTIVE_HFT_V1_SCORE','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,
         'trainMarkets':sorted({int(r['marketId']) for r in train}),'trainQueries':len(train),'testMarkets':sorted({int(r['marketId']) for r in test}),'testQueries':len(test),
         'support':{f'{k[0]}::{k[1]}':v for k,v in support.items()},'summary':summary,'perRegime':perRegime,'regimeWinsFallbackVsGlobal':wins,
         'boundedActionTestEligible':bool(wins>=3 and summary['REGIME_FALLBACK']['repair']['harmfulInterventions']<=summary['GLOBAL']['repair']['harmfulInterventions'] and summary['REGIME_FALLBACK']['add']['harmfulInterventions']<=summary['GLOBAL']['add']['harmfulInterventions']),
         'contract':'r4_github_regime_adaptive_hft_v1_contract.json'}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
