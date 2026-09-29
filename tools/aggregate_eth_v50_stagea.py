from __future__ import annotations
import argparse,json,glob
from pathlib import Path

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();files=sorted(glob.glob(a.pattern));rows=[]
 if not files:raise SystemExit('no V50 result files')
 for f in files:rows.extend(json.load(open(f,encoding='utf-8')).get('rows') or [])
 def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
 agg={'markets':len(rows),'checks':int(sm('candidateV50','v50RecoverabilityChecks')),'strandingBlocks':int(sm('candidateV50','v50StrandingBlocks')),'feasibleActivePaths':int(sm('candidateV50','v50FeasibleActivePaths')),'generationActivePaidQty':sm('candidateV50','v49GenerationActivePaidQty'),'preBirthOpportunityLeak':int(sm('candidateV50','v49PreBirthOpportunityLeak')),'sharedOverfill':sm('candidateV50','v36SharedRealizedOverfill'),'truthMismatch':sm('candidateV50','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV50','overOwnedSubmitViolations'),'repairDrift':sm('candidateV50','repairToExpandAtFirstFill'),'baselineFloorSum':sm('baselineV49','floor'),'candidateFloorSum':sm('candidateV50','floor'),'baselineAbsNetSum':sm('baselineV49','absNet'),'candidateAbsNetSum':sm('candidateV50','absNet')}
 per=[]
 blocked_live=0
 for x in rows:
  b=x['baselineV49'];c=x['candidateV50'];ev=c.get('v50Events',[]);bl=sum(1 for e in ev if e.get('reason')=='POST_ACTIVE_PASSIVE_REMAINDER_SUBLEGAL');live=sum(1 for e in ev if e.get('reason')=='POST_ACTIVE_PASSIVE_REMAINDER_SUBLEGAL' and e.get('passiveStillLiveAtBlock'));blocked_live+=live
  per.append({'marketId':x['marketId'],'checks':c.get('v50RecoverabilityChecks',0),'blocks':c.get('v50StrandingBlocks',0),'feasible':c.get('v50FeasibleActivePaths',0),'activePaid':c.get('v49GenerationActivePaidQty',0),'floorDelta':float(c.get('floor') or 0)-float(b.get('floor') or 0),'absNetDelta':float(c.get('absNet') or 0)-float(b.get('absNet') or 0),'blockEvents':bl,'blockLive':live})
 agg['blockedWithPassiveStillLive']=blocked_live;agg['marketsWithFeasibleActive']=sum(x['feasible']>0 for x in per);agg['marketsWithStrandingBlock']=sum(x['blocks']>0 for x in per)
 gates={'recoverabilityGateExercised':agg['checks']>0,'feasibleGenerationActiveRemains':agg['feasibleActivePaths']>0 and agg['generationActivePaidQty']>1e-9,'strandingPathBlocked':agg['strandingBlocks']>0,'blockedPathLeavesPassiveCarrierLive':blocked_live==agg['strandingBlocks'],'zeroPreBirthOpportunityLeak':agg['preBirthOpportunityLeak']==0,'zeroSharedOverfill':agg['sharedOverfill']<=1e-9,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0}
 out={'version':'ETH_REPAIR_V50_STAGEA_SYNTHESIS','priority':'FUNCTIONAL_ARCHITECTURE_BEFORE_NUMERIC_TUNING','sourceFiles':files,'aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'perMarket':per,'rows':rows,'boundary':['same V50 functional rules across all Stage-A markets','no threshold/qty/time/price tuning','floor/absNet diagnostic only','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates,'perMarket':per},ensure_ascii=False))
if __name__=='__main__':main()
