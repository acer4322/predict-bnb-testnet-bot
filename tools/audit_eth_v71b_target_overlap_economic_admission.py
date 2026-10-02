from __future__ import annotations
import argparse,json,statistics,math
from pathlib import Path
EPS=1e-9

def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return statistics.median(xs) if xs else None

def mean(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return statistics.mean(xs) if xs else None

def q(xs,p):
 xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not xs:return None
 if len(xs)==1:return xs[0]
 z=(len(xs)-1)*p;i=int(z);f=z-i
 return xs[i]*(1-f)+xs[min(i+1,len(xs)-1)]*f

def summarize(gs):
 n=len(gs)
 def vals(k):return [g.get(k) for g in gs]
 burden=[float(g.get('debtBeforeBirth') or 0.0)/max(float(g.get('qty') or 0.0),EPS) for g in gs]
 routes={}
 for g in gs:routes[str(g.get('route'))]=routes.get(str(g.get('route')),0)+1
 return {
  'n':n,
  'medianFloorBefore':med(vals('floorBefore')),
  'p25FloorBefore':q(vals('floorBefore'),.25),'p75FloorBefore':q(vals('floorBefore'),.75),
  'nonNegativeFloorShare':sum(float(g.get('floorBefore') or 0.0)>=-EPS for g in gs)/n if n else None,
  'medianDebtBeforeBirth':med(vals('debtBeforeBirth')),
  'medianDebtToNewExpandQty':med(burden),
  'p75DebtToNewExpandQty':q(burden,.75),
  'medianExpandFloorSacrifice':med([max(0.,-float(g.get('expandFloorDelta') or 0.0)) for g in gs]),
  'medianExpandBestGain':med([max(0.,float(g.get('expandBestDelta') or 0.0)) for g in gs]),
  'medianBestGainPerFloorSacrifice':med(vals('bestGainPerFloorSacrifice')),
  'medianGenerationNotional':med(vals('notional')),
  'medianPrice':med(vals('price')),
  'eventualFullPaidShare':sum(bool(g.get('fullyPaid')) for g in gs)/n if n else None,
  'fullPaidBeforeNextExpandShare':sum(bool(g.get('fullPaidBeforeNextExpand')) for g in gs)/n if n else None,
  'medianRepairToSacrificeRatio':med(vals('repairToSacrificeRatio')),
  'medianFirstPaymentLagSec':med(vals('firstPaymentLagSec')),
  'medianFullPaymentLagSec':med(vals('fullPaymentLagSec')),
  'routeCounts':routes,
 }

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--v71',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V71_GENERATION_ECONOMIC_ANATOMY_20260903.json');ap.add_argument('--output',required=True);a=ap.parse_args()
 d=json.load(open(a.v71,encoding='utf-8'));tg=d['targetGenerations'];og=d['ourGenerations']
 clean=[g for g in tg if not g.get('overlapAtBirth')];over=[g for g in tg if g.get('overlapAtBirth')];our_over=[g for g in og if g.get('overlapAtBirth')]
 s_clean=summarize(clean);s_over=summarize(over);s_our=summarize(our_over)
 # descriptive separation only; no runtime threshold authority
 diffs={
  'targetOverlapMinusCleanMedianFloorBefore':(s_over['medianFloorBefore']-s_clean['medianFloorBefore']) if s_over['medianFloorBefore'] is not None and s_clean['medianFloorBefore'] is not None else None,
  'targetOverlapMinusCleanMedianEfficiency':(s_over['medianBestGainPerFloorSacrifice']-s_clean['medianBestGainPerFloorSacrifice']) if s_over['medianBestGainPerFloorSacrifice'] is not None and s_clean['medianBestGainPerFloorSacrifice'] is not None else None,
  'targetOverlapMinusV70FMedianFloorBefore':(s_over['medianFloorBefore']-s_our['medianFloorBefore']) if s_over['medianFloorBefore'] is not None and s_our['medianFloorBefore'] is not None else None,
  'targetOverlapMinusV70FMedianDebtBurden':(s_over['medianDebtToNewExpandQty']-s_our['medianDebtToNewExpandQty']) if s_over['medianDebtToNewExpandQty'] is not None and s_our['medianDebtToNewExpandQty'] is not None else None,
  'targetOverlapMinusV70FMedianEfficiency':(s_over['medianBestGainPerFloorSacrifice']-s_our['medianBestGainPerFloorSacrifice']) if s_over['medianBestGainPerFloorSacrifice'] is not None and s_our['medianBestGainPerFloorSacrifice'] is not None else None,
  'targetOverlapMinusV70FFullPaidShare':(s_over['eventualFullPaidShare']-s_our['eventualFullPaidShare']) if s_over['eventualFullPaidShare'] is not None and s_our['eventualFullPaidShare'] is not None else None,
 }
 # Candidate descriptive axes if target overlap differs substantially from V70F or clean births.
 axes=[]
 if s_over['medianDebtToNewExpandQty'] is not None and s_our['medianDebtToNewExpandQty'] is not None and s_over['medianDebtToNewExpandQty']+0.25<s_our['medianDebtToNewExpandQty']:axes.append('LOWER_DEBT_BURDEN_RELATIVE_TO_NEW_EXPAND')
 if s_over['medianFloorBefore'] is not None and s_our['medianFloorBefore'] is not None and s_over['medianFloorBefore']>s_our['medianFloorBefore']+0.25:axes.append('HIGHER_PRE_BIRTH_FLOOR_CUSHION')
 if s_over['medianBestGainPerFloorSacrifice'] is not None and s_our['medianBestGainPerFloorSacrifice'] is not None and s_over['medianBestGainPerFloorSacrifice']>s_our['medianBestGainPerFloorSacrifice']+0.15:axes.append('HIGHER_UPSIDE_PER_FLOOR_SACRIFICE')
 if s_over['eventualFullPaidShare'] is not None and s_our['eventualFullPaidShare'] is not None and s_over['eventualFullPaidShare']>s_our['eventualFullPaidShare']+0.1:axes.append('HIGHER_EVENTUAL_REPAIR_COMPLETION')
 decision='KEEP_ECONOMIC_ADMISSION_AXES_FOR_FUNCTIONAL_MICROWORLD' if axes else 'NO_CLEAR_ECONOMIC_SELECTIVITY_KEEP_GENERATION_COMPLETION_FIRST'
 out={'version':'ETH_REPAIR_V71B_TARGET_OVERLAP_ECONOMIC_ADMISSION','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'groups':{'targetClean':s_clean,'targetOverlap':s_over,'ourV70FOverlap':s_our},'differences':diffs,'candidateAxes':axes,'decision':decision,'boundary':['descriptive only','no cutpoint becomes runtime threshold','no classifier training','no winner/PnL trigger','no controller change','no H100','no 8781']}
 Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidateAxes':axes,'groups':out['groups'],'differences':diffs},ensure_ascii=False))
if __name__=='__main__':main()
