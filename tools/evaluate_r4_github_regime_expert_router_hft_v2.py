from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import sys
ROOT=Path.cwd(); sys.path.insert(0,str(ROOT/'tools'))
import evaluate_r4_github_regime_adaptive_hft_v1 as v1
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_github_regime_expert_router_hft_v2_score.json'

def candidate_preds(train,test,cap):
    gm=v1.fit(train,cap); gp=v1.pred(gm,test,cap); bp=np.asarray([v1.basep(r,cap) for r in test],float); rp=[];rf=[]
    for r in test:
        rg=v1.regime(r); rr=[x for x in train if v1.regime(x)==rg]; yy=[v1.label(x,cap) for x in rr]; pos=sum(yy); neg=len(yy)-pos
        m=v1.fit(rr,cap) if pos>=5 and neg>=20 else None
        q=float(v1.pred(m,[r],cap)[0]) if m is not None else float(v1.basep(r,cap))
        rp.append(q); rf.append(float(v1.basep(r,cap)) if (m is None or abs(q-.5)<.10) else q)
    return {'BASELINE':bp,'GLOBAL':np.asarray(gp),'REGIME_FALLBACK':np.asarray(rf)}

def main():
    train=json.loads(v1.TRAIN.read_text(encoding='utf-8'))['queryRows']; test=[]
    for p in v1.TESTS:test+=json.loads(p.read_text(encoding='utf-8'))['queryRows']
    mids=sorted({int(r['marketId']) for r in train}); regs=['FORMATION','MGMT_NEG_FLOOR','MGMT_NONNEG_FLOOR','PROTECTION']; caps=['repair','add']; experts=['BASELINE','GLOBAL','REGIME_FALLBACK']
    cv={cap:{rg:{e:[] for e in experts} for rg in regs} for cap in caps}
    for mid in mids:
        tr=[r for r in train if int(r['marketId'])!=mid]; va=[r for r in train if int(r['marketId'])==mid]
        for cap in caps:
            ps=candidate_preds(tr,va,cap)
            for rg in regs:
                ix=[i for i,r in enumerate(va) if v1.regime(r)==rg]
                if not ix: continue
                y=np.asarray([v1.label(va[i],cap) for i in ix],int)
                for e in experts: cv[cap][rg][e].append(float(np.mean((ps[e][ix]>=.5).astype(int)!=y)))
    priority={'BASELINE':0,'GLOBAL':1,'REGIME_FALLBACK':2}; route={};cvMean={}
    for cap in caps:
        route[cap]={};cvMean[cap]={}
        for rg in regs:
            vals={e:(float(np.mean(cv[cap][rg][e])) if cv[cap][rg][e] else 1.0) for e in experts}; cvMean[cap][rg]=vals
            route[cap][rg]=min(experts,key=lambda e:(vals[e],priority[e]))
    full={cap:candidate_preds(train,test,cap) for cap in caps}; routed={}
    for cap in caps:
        z=[]
        for i,r in enumerate(test): z.append(float(full[cap][route[cap][v1.regime(r)]][i]))
        routed[cap]=np.asarray(z)
    summary={}
    for e in experts:
        summary[e]={cap:v1.metrics(test,cap,full[cap][e]) for cap in caps}
    summary['ROUTER']={cap:v1.metrics(test,cap,routed[cap]) for cap in caps}
    per={};wins=0
    for rg in regs:
        ix=[i for i,r in enumerate(test) if v1.regime(r)==rg]; rr=[test[i] for i in ix]; per[rg]={'n':len(rr),'routes':{cap:route[cap][rg] for cap in caps}}
        for e in ['GLOBAL','ROUTER']:
            es=[]
            for cap in caps:
                p=(full[cap]['GLOBAL'][ix] if e=='GLOBAL' else routed[cap][ix]); y=np.asarray([v1.label(r,cap) for r in rr],int); es.append(float(np.mean((p>=.5).astype(int)!=y)))
            per[rg][e+'MeanActionError']=float(np.mean(es))
        d=per[rg]['GLOBALMeanActionError']-per[rg]['ROUTERMeanActionError'];per[rg]['errorImprovementRouterVsGlobal']=d
        if d>0:wins+=1
    keep=(summary['ROUTER']['repair']['auc']>=summary['BASELINE']['repair']['auc'] and summary['ROUTER']['add']['auc']>=summary['BASELINE']['add']['auc'] and summary['ROUTER']['repair']['harmfulInterventions']<=summary['GLOBAL']['repair']['harmfulInterventions'] and summary['ROUTER']['add']['harmfulInterventions']<=summary['GLOBAL']['add']['harmfulInterventions'] and wins>=3)
    rep={'version':'R4_GITHUB_REGIME_EXPERT_ROUTER_HFT_V2_SCORE','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'trainMarkets':mids,'testMarkets':sorted({int(r['marketId']) for r in test}),'trainQueries':len(train),'testQueries':len(test),'cvMeanError':cvMean,'route':route,'summary':summary,'perRegime':per,'regimeWinsRouterVsGlobal':wins,'boundedActionTestEligible':bool(keep),'contract':'r4_github_regime_expert_router_hft_v2_contract.json'}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
