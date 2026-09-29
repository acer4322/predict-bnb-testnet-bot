from __future__ import annotations
import json, math, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
V85=P/'TARGET_ETH_V85B_COMPOSITE_BUDGET_STATE_ANATOMY_20260903.json'
V87=P/'TARGET_ETH_V87_COMPOSITE_RELAY_LEGAL_SLICE_20260903.json'
OUT=P/'TARGET_ETH_V88E_SMALL_OVERFLOW_RESPONSIBILITY_TRANSITION_20260903.json'
EPS=1e-12

def med(xs):
 xs=[float(x) for x in xs if x is not None and isinstance(x,(int,float)) and math.isfinite(float(x))]
 return statistics.median(xs) if xs else None

def mean(xs):
 xs=[float(x) for x in xs if x is not None and isinstance(x,(int,float)) and math.isfinite(float(x))]
 return sum(xs)/len(xs) if xs else None

def cliffs(a,b):
 a=[float(x) for x in a if x is not None and math.isfinite(float(x))]; b=[float(x) for x in b if x is not None and math.isfinite(float(x))]
 if not a or not b:return None
 gt=lt=0
 for x in a:
  for y in b:
   if x>y:gt+=1
   elif x<y:lt+=1
 return (gt-lt)/(len(a)*len(b))

def best_threshold(rows,feat):
 vals=sorted(set(float(r[feat]) for r in rows if r.get(feat) is not None and isinstance(r.get(feat),(int,float)) and math.isfinite(float(r[feat]))))
 if len(vals)<2:return None
 best=None
 for a,b in zip(vals[:-1],vals[1:]):
  th=(a+b)/2
  for direction in ('ge','lt'):
   tp=tn=fp=fn=0
   for r in rows:
    if r.get(feat) is None:continue
    pred=float(r[feat])>=th if direction=='ge' else float(r[feat])<th
    y=bool(r['nextParentComposite'])
    if pred and y:tp+=1
    elif pred and not y:fp+=1
    elif not pred and not y:tn+=1
    else:fn+=1
   n=tp+tn+fp+fn
   if not n:continue
   acc=(tp+tn)/n
   tpr=tp/(tp+fn) if tp+fn else 0
   tnr=tn/(tn+fp) if tn+fp else 0
   ba=(tpr+tnr)/2
   cand={'threshold':th,'direction':direction,'accuracy':acc,'balancedAccuracy':ba,'tp':tp,'tn':tn,'fp':fp,'fn':fn}
   if best is None or (ba,acc)>(best['balancedAccuracy'],best['accuracy']):best=cand
 return best

def main():
 v85=json.load(open(V85,encoding='utf-8'));v87=json.load(open(V87,encoding='utf-8'))
 by={(int(r['marketId']),int(r['t'])):r for r in v85['rows']}
 rows=[]
 for x in v87['stageA16Pre180']['rows']:
  if x['next']['overflowCoversOneLegalSlice']:continue
  k=(int(x['marketId']),int(x['t']));s=by.get(k)
  if not s:continue
  row={
   'marketId':k[0],'t':k[1],'nextParentComposite':bool(x['next']['nextParentComposite']),
   'route':s.get('route'),'nextRoute':x['next'].get('route'),'nextLagSec':x['next'].get('lagSec'),
   'nextPrice':x['next'].get('price'),'pairSum':(float(s['price'])+float(x['next']['price'])) if s.get('price') is not None and x['next'].get('price') is not None else None,
  }
  for f in ['floorBefore','floorAfter','floorDelta','bestBefore','bestAfter','bestDelta','repairGap','overflow','overflowToRepair','repairFloorGain','overflowFloorSpend','overflowSpendToRepairGain','reserveAfterRepairBeforeOverflow','oldResponsibilityAgeSec','oldExpandPayments','oldRepairPayments','oldExpandQty','oldRepairPaid']:
   row[f]=s.get(f)
  row['postFloorNonnegative']=bool(s.get('postFloorNonnegative'))
  row['bestMinusFloorAfter']=(float(s['bestAfter'])-float(s['floorAfter'])) if s.get('bestAfter') is not None and s.get('floorAfter') is not None else None
  row['oldPaidFrac']=(float(s['oldRepairPaid'])/float(s['oldExpandQty'])) if s.get('oldExpandQty') not in (None,0) else None
  rows.append(row)
 comp=[r for r in rows if r['nextParentComposite']];pure=[r for r in rows if not r['nextParentComposite']]
 feats=['floorBefore','floorAfter','floorDelta','bestBefore','bestAfter','bestDelta','bestMinusFloorAfter','repairGap','overflow','overflowToRepair','repairFloorGain','overflowFloorSpend','overflowSpendToRepairGain','reserveAfterRepairBeforeOverflow','oldResponsibilityAgeSec','oldExpandPayments','oldRepairPayments','oldExpandQty','oldRepairPaid','oldPaidFrac','nextLagSec','nextPrice','pairSum']
 stats={}
 for f in feats:
  ca=[r[f] for r in comp if r.get(f) is not None];pa=[r[f] for r in pure if r.get(f) is not None]
  stats[f]={'compositeMedian':med(ca),'pureMedian':med(pa),'medianDiff':(med(ca)-med(pa)) if med(ca) is not None and med(pa) is not None else None,'cliffsDelta':cliffs(ca,pa),'bestDescriptiveThreshold':best_threshold(rows,f)}
 route={}
 for f in ['route','nextRoute']:
  cats=sorted(set(str(r[f]) for r in rows))
  route[f]={c:{'n':sum(str(r[f])==c for r in rows),'compositeShare':sum(str(r[f])==c and r['nextParentComposite'] for r in rows)/sum(str(r[f])==c for r in rows)} for c in cats}
 # Rank axes by absolute Cliff's delta, but explicitly descriptive due n=15.
 ranked=sorted(({'axis':f,**d} for f,d in stats.items() if d['cliffsDelta'] is not None),key=lambda x:abs(x['cliffsDelta']),reverse=True)
 strong=[x for x in ranked if abs(x['cliffsDelta'])>=0.6 and (x['bestDescriptiveThreshold'] or {}).get('balancedAccuracy',0)>=0.7]
 decision='KEEP_DESCRIPTIVE_TRANSITION_STATE_AXES_FOR_MICROWORLD' if strong else 'NO_ROBUST_SMALL_OVERFLOW_TRANSITION_AXIS_DO_NOT_TUNE'
 out={'version':'TARGET_ETH_V88E_SMALL_OVERFLOW_RESPONSIBILITY_TRANSITION','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'decision':decision,'summary':{'n':len(rows),'nextComposite':len(comp),'nextPureRepair':len(pure),'nextCompositeShare':len(comp)/len(rows) if rows else None,'strongDescriptiveAxes':[x['axis'] for x in strong[:8]],'topAxes':ranked[:10]},'routeGroups':route,'featureStats':stats,'rows':rows,'interpretationBoundary':['Stage-A16 pre-180 Target post-market only','n=15 small cohort: thresholds are descriptive, never runtime authority','No future Target action used by OUR runtime','Consistent with V21/Regularities persistent responsibility + joint Floor/Upside framing']}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'decision':decision,'summary':out['summary'],'routeGroups':route},ensure_ascii=False))
if __name__=='__main__':main()
