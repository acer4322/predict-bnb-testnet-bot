from __future__ import annotations
import argparse,json,glob
from pathlib import Path

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();fs=sorted(glob.glob(a.pattern));rows=[]
 if not fs:raise SystemExit('no V49 result files')
 for f in fs:rows.extend(json.load(open(f,encoding='utf-8')).get('rows') or [])
 def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
 ds=[float(d) for x in rows for d in x['candidateV49'].get('v49GenerationActiveFloorDeltas',[])]
 agg={'markets':len(rows),'generationRearms':int(sm('candidateV49','v49GenerationRearms')),'hardConfirmed':int(sm('candidateV49','v49GenerationHardConfirmed')),'activeSubmits':int(sm('candidateV49','v49GenerationActiveSubmits')),'activeFillQty':sm('candidateV49','v49GenerationActiveFillQty'),'repairAfterExpandBaseline':sm('baselineV48','v44RepairQtyAfterExpand'),'repairAfterExpandCandidate':sm('candidateV49','v44RepairQtyAfterExpand'),'harmfulActiveFillEvents':sum(d<-1e-9 for d in ds),'truthMismatch':sm('candidateV49','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV49','overOwnedSubmitViolations'),'repairDrift':sm('candidateV49','repairToExpandAtFirstFill'),'sharedOverfill':sm('candidateV49','v36SharedRealizedOverfill'),'baselineFloorSum':sm('baselineV48','floor'),'candidateFloorSum':sm('candidateV49','floor'),'baselineAbsNetSum':sm('baselineV48','absNet'),'candidateAbsNetSum':sm('candidateV49','absNet')}
 gates={'generationRearmExercised':agg['generationRearms']>0,'hardEvidenceExercised':agg['hardConfirmed']>0,'activeSubmitExercised':agg['activeSubmits']>0,'activeFillExercised':agg['activeFillQty']>1e-9,'zeroHarmfulActiveFill':agg['harmfulActiveFillEvents']==0,'zeroSharedOverfill':agg['sharedOverfill']<=1e-9,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'repairPaymentNotReduced':agg['repairAfterExpandCandidate']+1e-9>=agg['repairAfterExpandBaseline']}
 out={'version':'ETH_REPAIR_V49_SMOKE4_SYNTHESIS','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False))
if __name__=='__main__':main()
