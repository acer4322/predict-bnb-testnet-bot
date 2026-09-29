from __future__ import annotations
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/lan_worker_returns/r4-v13-runtime20-artifacts'
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
CON=json.loads((P/'r4_target_sequence_v13_runtime_validation20_evaluation_contract.json').read_text())
OUT=P/'r4_target_sequence_v13_runtime_validation20_synthesis.json'
PHASES=['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']

def phase(sec):return 'FORMATION_180_300' if sec>180 else 'MANAGEMENT_60_180' if sec>=60 else 'PROTECTION_0_60'
def main():
 chunks=[json.loads((SRC/f'r4_target_sequence_v13_chunk{i}.json').read_text()) for i in range(2)]
 markets=[m for c in chunks for m in c['markets']]
 qs=[q for m in markets for q in m.get('queries',[])]
 acts=[a for m in markets for a in m.get('actualActions',[])]
 by={}; per_market=[]
 for m in markets:
  z=[q for q in m.get('queries',[]) if q['phase']=='FORMATION_180_300' and q.get('supportedDeepPositive')]
  tk=[q for q in z if q.get('deepRole')=='TAKER' and q.get('deepPurpose')!='FLAT_START']; add=[q for q in tk if q.get('deepPurpose')=='ADD']; rep=[q for q in tk if q.get('deepPurpose')=='REPAIR']; flat=[q for q in z if q.get('deepRole')=='TAKER' and q.get('deepPurpose')=='FLAT_START']
  per_market.append({'marketId':m['marketId'],'formationSupported':len(z),'formationTakerNonFlat':len(tk),'formationTakerAdd':len(add),'formationTakerRepair':len(rep),'formationTakerFlat':len(flat),'r3FormationTaker':sum(a['role']=='TAKER' and phase(a['secondsLeft'])=='FORMATION_180_300' for a in m.get('actualActions',[]))})
 for ph in PHASES:
  z=[q for q in qs if q['phase']==ph]; sup=[q for q in z if q.get('supportedDeepPositive')]; tk=[q for q in sup if q.get('deepRole')=='TAKER']; mk=[q for q in sup if q.get('deepRole')=='MAKER']; ad=[q for q in tk if q.get('deepPurpose')=='ADD']; rp=[q for q in tk if q.get('deepPurpose')=='REPAIR']; fl=[q for q in tk if q.get('deepPurpose')=='FLAT_START']; a=[x for x in acts if phase(x['secondsLeft'])==ph]
  by[ph]={'queries':len(z),'supportRate':float(np.mean([q['inTargetSupport'] for q in z])) if z else None,'hazardPositiveRate':float(np.mean([q['hazardPositive'] for q in z])) if z else None,'supportedActionOpportunities':len(sup),'supportedOpportunityPerMarket':len(sup)/len(markets),'proposedRoleTaker':len(tk),'proposedRoleMaker':len(mk),'proposedTakerPerMarket':len(tk)/len(markets),'proposedTakerAdd':len(ad),'proposedTakerRepair':len(rp),'proposedTakerFlat':len(fl),'proposedTakerAddFractionNonFlat':len(ad)/(len(ad)+len(rp)) if len(ad)+len(rp) else None,'r3ActualActions':len(a),'r3ActualTaker':sum(x['role']=='TAKER' for x in a),'r3ActualTakerPerMarket':sum(x['role']=='TAKER' for x in a)/len(markets),'blockedNewAddCheckpoints':sum(bool(q.get('blockedNewAddByPhase')) for q in z)}
 form=by['FORMATION_180_300']; ref=CON['reference']; g=CON['keepForNextShadowStage']; adddiff=abs(form['proposedTakerAddFractionNonFlat']-ref['targetSame30FormationNonFlatTakerAddFraction'])
 keep={'replayErrors':sum('error' in m for m in markets)==g['replayErrors'],'formationSupportRate':form['supportRate']>=g['formationSupportRateMin'],'formationTakerOpportunityDensity':g['formationTakerOpportunityPerMarketMin']<=form['proposedTakerPerMarket']<=g['formationTakerOpportunityPerMarketMax'],'formationTakerAddFraction':adddiff<=g['formationTakerAddFractionAbsoluteDifferenceFromReferenceMax']}; keep['all']=all(keep.values())
 vals=[x['formationTakerNonFlat'] for x in per_market]
 out={'version':'R4_TARGET_SEQUENCE_V1_3_RUNTIME_VALIDATION20_SYNTHESIS','researchOnly':True,'actionAuthority':False,'cohortContract':'r4_target_sequence_v13_runtime_validation20_cohort.json','evaluationContract':'r4_target_sequence_v13_runtime_validation20_evaluation_contract.json','markets':len(markets),'errors':sum('error' in m for m in markets),'queries':len(qs),'actualActions':len(acts),'byPhase':by,'formationPerMarket':{'meanTakerNonFlat':float(np.mean(vals)),'medianTakerNonFlat':float(np.median(vals)),'p10':float(np.quantile(vals,.1)),'p90':float(np.quantile(vals,.9)),'max':int(max(vals)),'marketsWithAnyTakerProposal':sum(v>0 for v in vals)},'referenceComparison':{'targetSame30NonFlatTakerPerMarket':ref['targetSame30FormationNonFlatTakerParentsPerMarket'],'v13Validation20NonFlatTakerPerMarket':form['proposedTakerPerMarket'],'densityRatioToTargetReference':form['proposedTakerPerMarket']/ref['targetSame30FormationNonFlatTakerParentsPerMarket'],'targetSame30TakerAddFraction':ref['targetSame30FormationNonFlatTakerAddFraction'],'v13Validation20TakerAddFraction':form['proposedTakerAddFractionNonFlat'],'addFractionAbsoluteDifference':adddiff,'r3FormationTakerPerMarket':form['r3ActualTakerPerMarket']},'keepForNextShadowStage':keep,'perMarket':per_market,'interpretation':['V1.3 is evaluated as a shadow cadence/semantic transport stack, not as an action or PnL candidate.','Checkpoint opportunities are the appropriate magnitude unit for a 1-second hazard teacher; rising edges are retained only as state-transition diagnostics.','Target reference is consumed same30 magnitude only; validation20 has no Target official same-market ingestion at scoring time.','No threshold or model parameter was changed after validation20 scoring.'],'guards':CON['guards']}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({k:out[k] for k in ['markets','errors','queries','actualActions','formationPerMarket','referenceComparison','keepForNextShadowStage']},indent=2));print(json.dumps(out['byPhase'],indent=2))
if __name__=='__main__':main()
