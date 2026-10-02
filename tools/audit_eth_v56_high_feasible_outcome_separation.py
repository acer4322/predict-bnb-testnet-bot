from __future__ import annotations
import argparse,glob,json,math
from pathlib import Path
from sklearn.metrics import roc_auc_score

def q(xs):
 a=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not a:return {'n':0}
 def z(p):return a[int(round((len(a)-1)*p))]
 return {'n':len(a),'mean':sum(a)/len(a),'p25':z(.25),'median':z(.5),'p75':z(.75)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows=[]
 for p in sorted(glob.glob(a.pattern)):
  d=json.load(open(p,encoding='utf-8'))
  for mr in d.get('rows',[]):
   mid=int(mr['marketId']);f=mr['functional'];led={int(x['parentId']):x for x in f.get('v34ParentLedger',[]) if x.get('parentId') is not None}
   by={}
   for x in f.get('v56Rows',[]):
    if not (x.get('highAtFrozen05') and x.get('feasible')):continue
    pid=x.get('parentId');
    if pid is None:continue
    k=(str(x.get('responsibilityKind')),int(x.get('generationId') or 0),int(x.get('epoch') or 0),int(pid));z=by.setdefault(k,[]);z.append(x)
   for k,xs in by.items():
    xs=sorted(xs,key=lambda x:int(x['t']));x=xs[0];pid=int(x['parentId']);pl=led.get(pid,{})
    passive=float(x.get('passiveRemaining') or 0);plegal=float(x.get('passiveLegalMin') or 0);gap=float(x.get('payoffGap') or x.get('generationRemaining') or 0);qty=float(x.get('qty') or 0);pf=float(x.get('projectedFloor') or 0);pre=float(x.get('worstCaseFloor') or 0)
    rows.append({'marketId':mid,'parentId':pid,'kind':x.get('responsibilityKind'),'firstHighFeasibleT':int(x['t']),'highFeasibleStateCount':len(xs),'hazard':float(x.get('hazard') or 0),'preFloor':pre,'projectedFloor':pf,'floorImprovement':pf-pre,'combinedAbsNet':float(x.get('combinedAbsNet') or 0),'generationRemaining':float(x.get('generationRemaining') or 0),'passiveRemaining':passive,'passiveLegalMin':plegal,'passiveRoomLegalMins':passive/plegal if plegal>0 else None,'activeQty':qty,'activeFracGap':qty/gap if gap>0 else None,'projectedRepairGap':float(x.get('projectedRepairGap') or 0),'activeAsk':float(x.get('activeAsk') or 0),'passivePrice':float(x.get('passivePrice') or 0),'activeAskMinusPassivePrice':float(x.get('activeAsk') or 0)-float(x.get('passivePrice') or 0),'makerRepairParents3s':float(x.get('makerRepairParents3s') or 0),'latestMakerExpansionAgeMs':float(x.get('latestMakerExpansionAgeMs') or 0),'passiveRepairQtyThisGeneration':float(x.get('passiveRepairQtyThisGeneration') or 0),'lastFillRole':x.get('lastFillRole'),'armed':bool(pl.get('armed')),'churnCount':int(pl.get('churnCount') or 0),'armBeforeHF':bool(pl.get('armAt') is not None and int(pl.get('armAt'))<=int(x['t'])),'passiveCompleted':bool(pl.get('passiveCompleted')),'terminalUnresolved':bool(pl.get('terminalUnresolved')),'completionKind':pl.get('completionKind'),'completionDelayFromHFMs':(int(pl['completedAt'])-int(x['t'])) if pl.get('completedAt') is not None else None})
 term=[r for r in rows if r['terminalUnresolved']];comp=[r for r in rows if r['passiveCompleted']]
 feats=['hazard','highFeasibleStateCount','preFloor','projectedFloor','floorImprovement','combinedAbsNet','generationRemaining','passiveRemaining','passiveRoomLegalMins','activeFracGap','projectedRepairGap','activeAsk','passivePrice','activeAskMinusPassivePrice','makerRepairParents3s','latestMakerExpansionAgeMs','passiveRepairQtyThisGeneration','churnCount']
 summary={}
 y=[1 if r['terminalUnresolved'] else 0 for r in rows]
 for f in feats:
  vals=[r.get(f) for r in rows];ix=[i for i,v in enumerate(vals) if v is not None and math.isfinite(float(v))]
  auc=None
  if ix and len(set(y[i] for i in ix))>1:
   try:auc=float(roc_auc_score([y[i] for i in ix],[float(vals[i]) for i in ix]))
   except:pass
  summary[f]={'terminal':q([r.get(f) for r in term]),'passiveCompleted':q([r.get(f) for r in comp]),'aucTerminalHigher':auc}
 def xtab(key):
  out={}
  for v in sorted({str(r.get(key)) for r in rows}):
   rr=[r for r in rows if str(r.get(key))==v];out[v]={'n':len(rr),'terminal':sum(r['terminalUnresolved'] for r in rr),'passiveCompleted':sum(r['passiveCompleted'] for r in rr),'terminalRate':sum(r['terminalUnresolved'] for r in rr)/len(rr) if rr else None}
  return out
 out={'version':'ETH_REPAIR_V56_HIGH_FEASIBLE_OUTCOME_SEPARATION','researchOnly':True,'actionAuthority':False,'n':len(rows),'terminal':len(term),'passiveCompleted':len(comp),'featureSummary':summary,'crossTabs':{'armed':xtab('armed'),'armBeforeHF':xtab('armBeforeHF'),'lastFillRole':xtab('lastFillRole'),'kind':xtab('kind')},'rows':rows,'boundary':['Outcome labels are retrospective diagnostics only.','No threshold sweep or behavior change.','Individual feature AUCs are descriptive ranking checks, not action authority.','No winner/PnL features.','No 8781.']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'n':out['n'],'terminal':out['terminal'],'passiveCompleted':out['passiveCompleted'],'crossTabs':out['crossTabs'],'topAuc':sorted([(k,v['aucTerminalHigher']) for k,v in summary.items() if v['aucTerminalHigher'] is not None],key=lambda x:abs(x[1]-.5),reverse=True)[:8]},indent=2))
if __name__=='__main__':main()
