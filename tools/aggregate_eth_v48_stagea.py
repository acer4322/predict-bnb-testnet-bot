from __future__ import annotations
import argparse,json,glob
from pathlib import Path

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();files=sorted(glob.glob(a.pattern));rows=[]
 if not files:raise SystemExit('no V48 result files')
 for f in files:
  z=json.load(open(f,encoding='utf-8'));rows.extend(z.get('rows') or [])
 def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
 agg={'markets':len(rows),'generationBirths':int(sm('candidateV48','v48GenerationBirths')),'materializedParallelCarriers':int(sm('candidateV48','v48MaterializedParallelCarriers')),'generationDebtQty':sm('candidateV48','v48GenerationDebtQty'),'generationPaidQty':sm('candidateV48','v48GenerationPaidQty'),'generationOverpay':sm('candidateV48','v48GenerationOverpay'),'discardedPreBirthRepairQty':sm('candidateV48','v48DiscardedPreBirthRepairQty'),'preBirthRepairLeak':sm('candidateV48','v48PreBirthRepairLeak'),'paymentEvents':int(sm('candidateV48','v48PaymentEvents')),'firstDecisions':int(sm('candidateV48','v48FirstDecisions')),'repeatedDecisions':int(sm('candidateV48','v48RepeatedDecisions')),'repeatedBlocks':int(sm('candidateV48','v48RepeatedBlocks')),'submits':int(sm('candidateV48','v44Submits')),'actualFillQty':sm('candidateV48','v44ActualFillQty'),'repairAfterExpandQty':sm('candidateV48','v44RepairQtyAfterExpand'),'truthMismatch':sm('candidateV48','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV48','overOwnedSubmitViolations'),'repairDrift':sm('candidateV48','repairToExpandAtFirstFill'),'baselineFloorSum':sm('baselineV44','floor'),'candidateFloorSum':sm('candidateV48','floor'),'baselineAbsNetSum':sm('baselineV44','absNet'),'candidateAbsNetSum':sm('candidateV48','absNet')}
 birth_prior_credit=sum(1 for x in rows for g in x['candidateV48'].get('v48GenerationEvents',[]) if g.get('event')=='GENERATION_BIRTH' and abs(float(g.get('progressAtBirth') or 0.))>1e-12)
 agg['birthWithPriorCredit']=birth_prior_credit
 gates={'birthMatchesMaterializedCarriers':agg['generationBirths']==agg['materializedParallelCarriers'],'debtMatchesActualParallelFill':abs(agg['generationDebtQty']-agg['actualFillQty'])<=1e-7,'zeroBirthWithPriorCredit':birth_prior_credit==0,'zeroGenerationOverpay':agg['generationOverpay']<=1e-9,'zeroPreBirthRepairLeak':agg['preBirthRepairLeak']<=1e-12,'generationPaymentExercised':agg['paymentEvents']>0 and agg['generationPaidQty']>1e-9,'repeatedDecisionPathExercised':agg['repeatedDecisions']>0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0}
 out={'version':'ETH_REPAIR_V48_STAGEA_SYNTHESIS','sourceFiles':files,'aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False))
if __name__=='__main__':main()
