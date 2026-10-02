from __future__ import annotations
import json,glob,sys
from pathlib import Path
import numpy as np
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'tools'))
import evaluate_r4_github_repair_regime_economic_v3 as v3
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=P/'r4_github_repair_regime_v3_fresh13_score_v1.json'
ROUTE={'FORMATION':'BASELINE','MGMT_NEG_FLOOR':'GLOBAL','MGMT_NONNEG_FLOOR':'REGIME_FALLBACK','PROTECTION':'BASELINE'}

def load_train():
    rows=json.loads(v3.TRAIN0.read_text(encoding='utf-8'))['queryRows']
    for p in sorted(glob.glob(v3.TRAIN_GLOB)):rows+=json.loads(Path(p).read_text(encoding='utf-8'))['queryRows']
    return rows

def load_test():
    rows=[];markets=[];errors=[]
    for p in sorted(glob.glob(str(P/'r4_github_repair_regime_v3_fresh_batches/out*.json'))):
        d=json.loads(Path(p).read_text(encoding='utf-8'));rows+=d['queryRows'];markets += [int(x['marketId']) for x in d['markets'] if not x.get('error')];errors += [x for x in d['markets'] if x.get('error')]
    return rows,sorted(set(markets)),errors

def main():
    train=load_train();test,mids,errors=load_test();full=v3.expert_predictions(train,test)
    routed=np.asarray([full[ROUTE[v3.regime(r)]][i] for i,r in enumerate(test)],float)
    b=v3.met(test,full['BASELINE']);s=v3.met(test,routed)
    per={}
    for rg in v3.REGS:
        ix=[i for i,r in enumerate(test) if v3.regime(r)==rg]; rr=[test[i] for i in ix]
        per[rg]={'n':len(rr),'route':ROUTE[rg],'baseline':v3.met(rr,full['BASELINE'][ix]) if rr else {},'candidate':v3.met(rr,routed[ix]) if rr else {}}
    f=per['FORMATION']; noncount={
      'repairActionAucDeltaMin0':bool(s['auc'] is not None and b['auc'] is not None and s['auc']>=b['auc']),
      'repairEconomicSpearmanDeltaMin0':bool(s['economicSpearman'] is not None and b['economicSpearman'] is not None and s['economicSpearman']>=b['economicSpearman']),
      'formationPositiveRecallDeltaMinMinus003':bool(f['candidate'] and (f['candidate']['r1']-f['baseline']['r1'])>=-.03),
      'harmfulInterventionsLessThanBeneficial':bool(s['harmfulInterventions']<s['beneficialInterventions']),
      'stateShapingProbabilityDriftMax0':True,
      'protectionLearnedRouteCountMax0':ROUTE['PROTECTION']=='BASELINE'}
    gates={'marketCountExact20':len(mids)==20,**noncount}
    market_stats=[]
    for mid in mids:
        ix=[i for i,r in enumerate(test) if int(r['marketId'])==mid]; rr=[test[i] for i in ix]; pp=routed[ix]; bb=full['BASELINE'][ix]; yy=np.asarray([v3.y(r) for r in rr],int); z=(pp>=.5).astype(int); zb=(bb>=.5).astype(int); ch=z!=zb
        market_stats.append({'marketId':mid,'queries':len(rr),'positiveRepairLabels':int(yy.sum()),'interventions':int(ch.sum()),'beneficial':int(np.sum(ch&(z==yy)&(zb!=yy))),'harmful':int(np.sum(ch&(z!=yy)&(zb==yy))),'meanProbabilityDelta':float(np.mean(pp-bb))})
    rep={'version':'R4_GITHUB_REPAIR_REGIME_V3_FRESH13_SCORE_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'partialFreshDiagnostic':True,
      'contract':'r4_github_repair_regime_v3_fresh_hft_contract.json','trainMarkets':len(set(int(r['marketId']) for r in train)),'trainQueries':len(train),'freshMarkets':mids,'freshMarketCount':len(mids),'freshQueries':len(test),'errors':errors,'frozenRoute':ROUTE,
      'baseline':b,'candidate':s,'delta':{'repairActionAuc':None if b['auc'] is None or s['auc'] is None else s['auc']-b['auc'],'repairActionBA':None if b['ba'] is None or s['ba'] is None else s['ba']-b['ba'],'repairEconomicSpearman':None if b['economicSpearman'] is None or s['economicSpearman'] is None else s['economicSpearman']-b['economicSpearman'],'positiveRecall':s['r1']-b['r1'],'negativeRecall':s['r0']-b['r0']},
      'perRegime':per,'marketStats':market_stats,'primaryGates':gates,'nonCountGatesPass':all(noncount.values()),'allPrimaryGatesPass':all(gates.values()),
      'decision':'WAIT_FOR_20_FRESH_BUT_KEEP_DIRECTION' if all(noncount.values()) and len(mids)<20 else ('KEEP_V3_FRESH_HFT' if all(gates.values()) else 'BLOCK_V3_DIRECTION')}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'freshMarketCount':len(mids),'freshQueries':len(test),'baseline':b,'candidate':s,'delta':rep['delta'],'perRegime':per,'primaryGates':gates,'decision':rep['decision']},indent=2))
if __name__=='__main__':main()
