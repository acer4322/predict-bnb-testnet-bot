from __future__ import annotations
import argparse,json,glob
from pathlib import Path

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();files=sorted(glob.glob(a.pattern));rows=[]
 if not files:raise SystemExit('no V49 result files')
 for f in files:
  z=json.load(open(f,encoding='utf-8'));rows.extend(z.get('rows') or [])
 def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
 agg={
  'markets':len(rows),
  'generationBirths':int(sm('candidateV49','v48GenerationBirths')),
  'rearmContexts':int(sm('candidateV49','v49RearmContexts')),
  'generationHardConfirmed':int(sm('candidateV49','v49GenerationHardConfirmed')),
  'generationActiveSubmits':int(sm('candidateV49','v49GenerationActiveSubmits')),
  'generationActivePaidQty':sm('candidateV49','v49GenerationActivePaidQty'),
  'baselineGenerationPaidQty':sm('baselineV48','v48GenerationPaidQty'),
  'candidateGenerationPaidQty':sm('candidateV49','v48GenerationPaidQty'),
  'blockedOlderActiveLive':int(sm('candidateV49','v49BlockedOlderActiveLive')),
  'blockedGenerationPaymentProgress':int(sm('candidateV49','v49BlockedGenerationPaymentProgress')),
  'preBirthOpportunityLeak':int(sm('candidateV49','v49PreBirthOpportunityLeak')),
  'sharedOverfill':sm('candidateV49','v36SharedRealizedOverfill'),
  'truthMismatch':sm('candidateV49','authorizedSubmitWithTruthRoleMismatch'),
  'overOwned':sm('candidateV49','overOwnedSubmitViolations'),
  'repairDrift':sm('candidateV49','repairToExpandAtFirstFill'),
  'baselineFloorSum':sm('baselineV48','floor'),
  'candidateFloorSum':sm('candidateV49','floor'),
  'baselineAbsNetSum':sm('baselineV48','absNet'),
  'candidateAbsNetSum':sm('candidateV49','absNet')
 }
 per=[]
 for x in rows:
  b=x['baselineV48'];c=x['candidateV49'];per.append({'marketId':x['marketId'],'births':c.get('v48GenerationBirths',0),'hard':c.get('v49GenerationHardConfirmed',0),'activePaid':c.get('v49GenerationActivePaidQty',0),'baseGenPaid':b.get('v48GenerationPaidQty',0),'candGenPaid':c.get('v48GenerationPaidQty',0),'floorDelta':float(c.get('floor') or 0)-float(b.get('floor') or 0),'absNetDelta':float(c.get('absNet') or 0)-float(b.get('absNet') or 0)})
 agg['marketsWithRearm']=sum(int(x['hard'])>0 for x in per);agg['marketsWithActiveGenerationPayment']=sum(float(x['activePaid'])>1e-9 for x in per)
 gates={
  'allGenerationContextsAccounted':agg['rearmContexts']==agg['generationBirths'],
  'hardConfirmBoundedByContexts':agg['generationHardConfirmed']<=agg['rearmContexts'],
  'activeSubmitBoundedByHardConfirm':agg['generationActiveSubmits']<=agg['generationHardConfirmed'],
  'generationRearmExercised':agg['generationHardConfirmed']>0 and agg['generationActiveSubmits']>0,
  'generationActivePaymentExercised':agg['generationActivePaidQty']>1e-9 and agg['candidateGenerationPaidQty']>agg['baselineGenerationPaidQty']+1e-9,
  'zeroPreBirthOpportunityLeak':agg['preBirthOpportunityLeak']==0,
  'zeroSharedOverfill':agg['sharedOverfill']<=1e-9,
  'zeroTruthMismatch':agg['truthMismatch']==0,
  'zeroOverOwned':agg['overOwned']==0,
  'zeroRepairDrift':agg['repairDrift']==0
 }
 out={'version':'ETH_REPAIR_V49_STAGEA_SYNTHESIS','sourceFiles':files,'priority':'FUNCTIONAL_ARCHITECTURE_BEFORE_NUMERIC_TUNING','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'perMarket':per,'rows':rows,'boundary':['same V49 frozen functional rules across all Stage-A markets','no threshold/qty/time/price tuning','floor/absNet diagnostic only','no dream fill','no 8781']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates,'perMarket':per},ensure_ascii=False))
if __name__=='__main__':main()
