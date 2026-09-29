from __future__ import annotations
import argparse,json,glob
from pathlib import Path

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();files=sorted(glob.glob(a.pattern));rows=[]
 if not files:raise SystemExit('no V51 result files')
 for f in files:rows.extend(json.load(open(f,encoding='utf-8')).get('rows') or [])
 def sm(side,k):return sum(float(x[side].get(k) or 0.) for x in rows)
 agg={'markets':len(rows),'retainChecks':int(sm('candidateV51','v51RetainChecks')),'retainedPassive':int(sm('candidateV51','v51RetainedPassive')),'retainedLive':int(sm('candidateV51','v51RetainedLive')),'retainedPassiveFillQty':sm('candidateV51','v51RetainedPassiveFillQty'),'responsibilityOverfill':sm('candidateV51','v51ResponsibilityOverfill'),'baselineFloorSum':sm('baselineV50','floor'),'candidateFloorSum':sm('candidateV51','floor'),'baselineAbsNetSum':sm('baselineV50','absNet'),'candidateAbsNetSum':sm('candidateV51','absNet'),'baselineGenPaid':sm('baselineV50','v48GenerationPaidQty'),'candidateGenPaid':sm('candidateV51','v48GenerationPaidQty'),'v50StrandingBlocks':int(sm('candidateV51','v50StrandingBlocks')),'truthMismatch':sm('candidateV51','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidateV51','overOwnedSubmitViolations'),'repairDrift':sm('candidateV51','repairToExpandAtFirstFill')}
 per=[]
 for x in rows:
  b=x['baselineV50'];c=x['candidateV51'];per.append({'marketId':x['marketId'],'retained':c.get('v51RetainedPassive',0),'retainedFill':c.get('v51RetainedPassiveFillQty',0),'responsibilityOverfill':c.get('v51ResponsibilityOverfill',0),'v50Blocks':c.get('v50StrandingBlocks',0),'floorDelta':float(c.get('floor') or 0)-float(b.get('floor') or 0),'absNetDelta':float(c.get('absNet') or 0)-float(b.get('absNet') or 0),'genPaidDelta':float(c.get('v48GenerationPaidQty') or 0)-float(b.get('v48GenerationPaidQty') or 0)})
 gates={'queueRetentionExercised':agg['retainedPassive']>0,'retainedCarrierWasLive':agg['retainedLive']==agg['retainedPassive'],'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=1e-9,'v50StrandingProtectionPreserved':agg['v50StrandingBlocks']>0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0}
 out={'version':'ETH_REPAIR_V51_STAGEA_SYNTHESIS','priority':'FUNCTIONAL_ARCHITECTURE_BEFORE_NUMERIC_TUNING','sourceFiles':files,'aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'perMarket':per,'rows':rows,'boundary':['same V51 rules all Stage-A markets','initial V36 active behavior unchanged','generation-active may retain compatible live passive carrier','no threshold/qty/delay/price tuning','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates,'perMarket':per},ensure_ascii=False))
if __name__=='__main__':main()
