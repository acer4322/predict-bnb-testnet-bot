from pathlib import Path
import json, sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.worker_r4_adaptive_hft_transfer_v1 import summarize, delta
JOBS=['r4-adaptive-hybrid-v3-holdout2-c0b','r4-adaptive-hybrid-v3-holdout2-c1b']
BASE=ROOT/'data/research/lan_worker_returns'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_adaptive_cycle_hybrid_v3_hft_holdout2_score_v1.json'
PH='FORMATION_180_300'
rows=[]; markets=[]; exact=True
for j in JOBS:
    fs=list((BASE/j).glob('chunk*.json'))
    if len(fs)!=1: raise RuntimeError(f'{j}: expected one chunk json, got {fs}')
    d=json.loads(fs[0].read_text(encoding='utf-8'))
    exact=exact and bool(d.get('trajectoryIdentityAll'))
    rows += d.get('queryRows') or []
    markets += d.get('markets') or []
ids=sorted({int(x['marketId']) for x in markets if 'marketId' in x})
uniq={(int(r['marketId']),int(r['atMs'])) for r in rows}
if len(uniq)!=len(rows): raise RuntimeError('duplicate query rows')
summ={'ALL':summarize(rows)['ALL']}
for ph in ['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']:
    summ[ph]=summarize([r for r in rows if r['phase']==ph])['ALL']
ds=delta(summ)
a=ds['ALL']
max_add=max(abs(float(r['addCand'])-float(r['addBase'])) for r in rows) if rows else float('inf')
fr=[r for r in rows if r['phase']==PH]
max_add_f=max(abs(float(r['addCand'])-float(r['addBase'])) for r in fr) if fr else float('inf')
def ge(v,x): return v is not None and v>=x
gates={
 'marketCountExact20':len(ids)==20,
 'trajectoryIdentityAll':exact,
 'repairActionAucDeltaMin0':ge(a['repairActionAuc'],0.0),
 'repairEconomicSpearmanDeltaMin0':ge(a['repairEconomicSpearman'],0.0),
 'repairPositiveRecallDeltaMinMinus003':ge(summ['ALL']['repairAction5s']['candidate']['r1']-summ['ALL']['repairAction5s']['baseline']['r1'],-0.03),
 'stateShapingMaxAbsProbabilityDeltaMax1e12':max_add<=1e-12,
 'formationStateShapingMaxAbsProbabilityDeltaMax1e12':max_add_f<=1e-12,
}
passed=all(gates.values())
rep={'version':'R4_ADAPTIVE_CYCLE_HYBRID_V3_HFT_HOLDOUT2_SCORE_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':True,'marketCount':len(ids),'markets':ids,'queries':len(rows),'trajectoryIdentityAll':exact,'summary':summ,'deltaCandidateMinusBaseline':ds,'maxAbsStateShapingProbabilityDelta':max_add,'formationMaxAbsStateShapingProbabilityDelta':max_add_f,'primaryGates':gates,'allPrimaryGatesPass':passed,'decision':'KEEP_HYBRID_V3_HFT' if passed else 'BLOCK_HYBRID_V3','contract':'r4_adaptive_cycle_hybrid_v3_hft_holdout2_contract.json'}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
print(json.dumps({'marketCount':len(ids),'queries':len(rows),'deltaALL':a,'maxAddDelta':max_add,'formationMaxAddDelta':max_add_f,'gates':gates,'passed':passed,'decision':rep['decision']},indent=2))
