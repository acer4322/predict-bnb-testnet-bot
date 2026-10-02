from __future__ import annotations
import argparse,glob,json,math
from pathlib import Path
from collections import defaultdict

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 paths=sorted(glob.glob(a.pattern));docs=[json.load(open(p,encoding='utf-8')) for p in paths]
 rows=[];agg=defaultdict(float);hf_epoch={};all_epoch={};market_hf=set();market_existing=set();parent_outcomes={};round_bins=defaultdict(lambda:{'markets':0,'hfMarkets':0,'hfEpochs':0,'existingHardEpochs':0})
 for d in docs:
  for k,v in d.get('aggregate',{}).items():
   if isinstance(v,(int,float)) and not isinstance(v,bool):agg[k]+=float(v)
  for mr in d.get('rows',[]):
   mid=int(mr['marketId']);f=mr['functional'];rows.append(mr)
   rb=int(f.get('repairExpandRepairRounds') or 0);b='R0' if rb==0 else 'R1' if rb==1 else 'R2PLUS';round_bins[b]['markets']+=1
   hfe=int(f.get('v56HighFeasibleEpochs') or 0);eh=int(f.get('v52EpochHardConfirmed') or 0);round_bins[b]['hfEpochs']+=hfe;round_bins[b]['existingHardEpochs']+=eh
   if hfe>0:market_hf.add(mid);round_bins[b]['hfMarkets']+=1
   if eh>0:market_existing.add(mid)
   led={int(x['parentId']):x for x in f.get('v34ParentLedger',[]) if x.get('parentId') is not None}
   for x in f.get('v56Rows',[]):
    pid=x.get('parentId');gid=int(x.get('generationId') or 0);ep=int(x.get('epoch') or 0);kind=str(x.get('responsibilityKind') or '')
    key=(mid,kind,gid,ep,int(pid) if pid is not None else -1)
    all_epoch.setdefault(key,{'marketId':mid,'kind':kind,'generationId':gid,'epoch':ep,'parentId':pid,'high':False,'feasible':False,'highFeasible':False,'firstT':int(x['t']),'firstHighFeasibleT':None,'existingHardConfirmed':False})
    z=all_epoch[key];z['firstT']=min(z['firstT'],int(x['t']));z['high']=z['high'] or bool(x.get('highAtFrozen05'));z['feasible']=z['feasible'] or bool(x.get('feasible'));z['existingHardConfirmed']=z['existingHardConfirmed'] or bool(x.get('existingHardConfirmed'))
    if bool(x.get('highAtFrozen05')) and bool(x.get('feasible')):
     z['highFeasible']=True;t=int(x['t']);z['firstHighFeasibleT']=t if z['firstHighFeasibleT'] is None else min(z['firstHighFeasibleT'],t);hf_epoch[key]=z
     if pid is not None and int(pid) in led:
      p=led[int(pid)];parent_outcomes[(mid,int(pid))]={'marketId':mid,'parentId':int(pid),'passiveCompleted':bool(p.get('passiveCompleted')),'terminalUnresolved':bool(p.get('terminalUnresolved')),'armed':bool(p.get('armed')),'completionKind':p.get('completionKind'),'completed':bool(p.get('completed'))}
 # Some V56 rows are stored selectively; reconcile counts against declared aggregates.
 declared_hf=int(round(agg.get('v56HighFeasibleEpochs',0.0)));declared_existing=int(round(agg.get('existingV52HardEpochs',0.0)))
 outc=list(parent_outcomes.values());pc=sum(x['passiveCompleted'] for x in outc);tu=sum(x['terminalUnresolved'] for x in outc);both=sum(x['passiveCompleted'] and x['terminalUnresolved'] for x in outc)
 result={
  'version':'ETH_REPAIR_V56_TARGET_BOOK_HAZARD_H100_SYNTHESIS',
  'researchOnly':True,'behaviorChange':False,'actionAuthority':False,
  'sources':paths,
  'coverage':{'markets':len(rows),'marketsWithHighFeasible':len(market_hf),'marketsWithExistingV52Hard':len(market_existing),'declaredHighFeasibleEpochs':declared_hf,'declaredExistingV52HardEpochs':declared_existing,'storedHighFeasibleEpochs':len(hf_epoch)},
  'aggregate':dict(agg),
  'ratios':{
   'highFeasibleMarketRate':len(market_hf)/len(rows) if rows else None,
   'coverageMultiplierVsExistingHard':declared_hf/declared_existing if declared_existing>0 else None,
   'earlierOrNeverHardFraction':(agg.get('v56HighFeasibleEarlierThanExistingHardOrNeverHardEpochs',0.0)/declared_hf) if declared_hf>0 else None,
   'highFeasibleStateFractionOfEligible':(agg.get('v56HighFeasibleStates',0.0)/agg.get('v56EligibleStates',1.0)) if agg.get('v56EligibleStates',0)>0 else None
  },
  'highFeasibleParentOutcome':{'uniqueParentsObserved':len(outc),'passiveCompleted':pc,'terminalUnresolved':tu,'both':both,'passiveCompletedRate':pc/len(outc) if outc else None,'terminalUnresolvedRate':tu/len(outc) if outc else None},
  'roundBins':dict(round_bins),
  'highFeasibleEpochsStored':sorted(hf_epoch.values(),key=lambda x:(x['marketId'],x['firstHighFeasibleT'] or 0)),
  'highFeasibleParentOutcomes':sorted(outc,key=lambda x:(x['marketId'],x['parentId'])),
  'boundary':['No behavior change.','Frozen Target ETH 1s Repair-Taker hazard; no threshold sweep.','Parent outcome is unchanged V52/V53 lifecycle outcome and is diagnostic only.','Stored row reconciliation must be checked against declared aggregate counts before causal promotion.','No winner/PnL authority.','No 8781.']
 }
 Path(a.output).write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps({k:result[k] for k in ['coverage','ratios','highFeasibleParentOutcome','roundBins']},indent=2))
if __name__=='__main__':main()
