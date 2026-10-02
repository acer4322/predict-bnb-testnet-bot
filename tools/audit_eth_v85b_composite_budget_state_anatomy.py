from __future__ import annotations
import json,sqlite3,math,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/target_wallet_official_v1.db';V69=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json';OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V85B_COMPOSITE_BUDGET_STATE_ANATOMY_20260903.json'
MIDS=[1823603,1823769,1823894,1823897,1824037,1824747,1824852,1825353,1825959,1826030,1826386,1827223,1827418,1827903,1828268,1828768];EPS=1e-9

def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return statistics.median(xs) if xs else None

def rank(a):
 idx=sorted(range(len(a)),key=lambda i:a[i]);r=[0.0]*len(a);i=0
 while i<len(idx):
  j=i+1
  while j<len(idx) and a[idx[j]]==a[idx[i]]:j+=1
  rr=(i+j-1)/2+1
  for k in range(i,j):r[idx[k]]=rr
  i=j
 return r

def corr(a,b):
 z=[(float(x),float(y)) for x,y in zip(a,b) if x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y))]
 if len(z)<4:return None
 x=[v[0] for v in z];y=[v[1] for v in z];mx=sum(x)/len(x);my=sum(y)/len(y);dx=[v-mx for v in x];dy=[v-my for v in y];den=(sum(v*v for v in dx)*sum(v*v for v in dy))**.5
 return sum(x*y for x,y in zip(dx,dy))/den if den>EPS else None

def spear(a,b):
 z=[(float(x),float(y)) for x,y in zip(a,b) if x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y))]
 if len(z)<4:return None
 return corr(rank([x for x,_ in z]),rank([y for _,y in z]))

def bin_summary(rows,axis,outcome):
 z=[r for r in rows if r.get(axis) is not None and r.get(outcome) is not None and math.isfinite(float(r[axis])) and math.isfinite(float(r[outcome]))]
 if len(z)<4:return None
 cut=med([r[axis] for r in z]);lo=[r[outcome] for r in z if r[axis]<=cut];hi=[r[outcome] for r in z if r[axis]>cut]
 return {'cut':cut,'lowN':len(lo),'highN':len(hi),'lowMedian':med(lo),'highMedian':med(hi),'highMinusLow':None if not lo or not hi else med(hi)-med(lo)}

v69=json.loads(V69.read_text(encoding='utf-8'));end_by={}
for r in v69['conditionRows']:
 mid=int(r['marketId'])
 if mid not in end_by:
  s=r['ourState'];end_by[mid]=int(round(float(s['snapshotT'])+float(s['remainingSec'])*1000.0))
con=sqlite3.connect(DB);con.row_factory=sqlite3.Row;allrows=[];per=[]
for mid in MIDS:
 rs=con.execute("select role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where market_id=? and asset='ETH' order by first_event_ms,parent_id",(mid,)).fetchall();u=d=cost=0.0;cur=None;cr=[]
 for r in rs:
  t=int(r['first_event_ms']);p=float(r['average_price'] or 0);q=float(r['shares'] or 0);side=str(r['side']);route=str(r['role']);gap=abs(u-d);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;f0=min(u,d)-cost;b0=max(u,d)-cost
  repair=min(q,gap) if side==weak else 0.0;overflow=max(0.0,q-repair)
  # strict-past persistent responsibility snapshot
  old={'side':None,'debt':0.0,'ageSec':None,'expandPayments':0,'repairPayments':0,'expandQty':0.0,'repairPaid':0.0}
  if cur is not None:
   old={'side':cur['side'],'debt':cur['debt'],'ageSec':(t-cur['bornAt'])/1000.0,'expandPayments':cur['expandPayments'],'repairPayments':cur['repairPayments'],'expandQty':cur['expandQty'],'repairPaid':cur['repairPaid']}
  repair_gain=repair*(1.0-p);overflow_spend=overflow*p;safe_overflow=(repair_gain/p) if p>EPS else None;reserve_after_repair=f0+repair_gain;nonneg_capacity=max(0.0,reserve_after_repair)/p if p>EPS else None
  # update physical inventory
  if side=='UP':u+=q
  else:d+=q
  cost+=p*q;f1=min(u,d)-cost;b1=max(u,d)-cost
  # update persistent responsibility economics in component order
  if repair>EPS and cur is not None and side!=cur['side']:
   pay=min(repair,cur['debt']);cur['debt']-=pay;cur['repairPaid']+=pay;cur['repairPayments']+=1
  if overflow>EPS:
   if cur is None or side!=cur['side']:
    cur={'side':side,'bornAt':t,'debt':0.0,'expandPayments':0,'repairPayments':0,'expandQty':0.0,'repairPaid':0.0}
   cur['debt']+=overflow;cur['expandQty']+=overflow;cur['expandPayments']+=1
  elif repair<=EPS and q>EPS: # dominant-side pure Expand payment
   if cur is None or side!=cur['side']:
    cur={'side':side,'bornAt':t,'debt':0.0,'expandPayments':0,'repairPayments':0,'expandQty':0.0,'repairPaid':0.0}
   cur['debt']+=q;cur['expandQty']+=q;cur['expandPayments']+=1
  if repair>EPS and overflow>EPS:
   z={'marketId':mid,'t':t,'route':route,'side':side,'price':p,'qty':q,'repairGap':gap,'repairAllocation':repair,'overflow':overflow,'overflowToRepair':overflow/repair if repair>EPS else None,'floorBefore':f0,'bestBefore':b0,'floorAfter':f1,'bestAfter':b1,'floorDelta':f1-f0,'bestDelta':b1-b0,'repairFloorGain':repair_gain,'overflowFloorSpend':overflow_spend,'overflowSpendToRepairGain':overflow_spend/repair_gain if repair_gain>EPS else None,'floorPreservingOverflowCapacity':safe_overflow,'overflowToFloorPreservingCapacity':overflow/safe_overflow if safe_overflow and safe_overflow>EPS else None,'reserveAfterRepairBeforeOverflow':reserve_after_repair,'nonnegativeFloorOverflowCapacity':nonneg_capacity,'postFloorNonnegative':f1>=-EPS,'oldResponsibilitySide':old['side'],'oldResponsibilityDebt':old['debt'],'oldDebtGapMismatch':abs(old['debt']-gap) if old['side'] is not None else None,'oldResponsibilityAgeSec':old['ageSec'],'oldExpandPayments':old['expandPayments'],'oldRepairPayments':old['repairPayments'],'oldExpandQty':old['expandQty'],'oldRepairPaid':old['repairPaid'],'pre180':t<=end_by.get(mid,10**30)-180000,'parentId':str(r['parent_id'])};cr.append(z);allrows.append(z)
 per.append({'marketId':mid,'composites':len(cr),'pre180':sum(x['pre180'] for x in cr)})
con.close()
primary=[r for r in allrows if r['pre180']]
axes=['repairGap','floorBefore','repairFloorGain','reserveAfterRepairBeforeOverflow','oldResponsibilityAgeSec','oldExpandPayments','oldRepairPayments','oldResponsibilityDebt']
outcomes=['overflow','overflowToRepair']
assoc={}
for a in axes:
 assoc[a]={}
 for o in outcomes:
  assoc[a][o]={'spearman':spear([r.get(a) for r in primary],[r.get(o) for r in primary]),'pearson':corr([r.get(a) for r in primary],[r.get(o) for r in primary]),'medianSplit':bin_summary(primary,a,o)}
route={}
for rt in ('MAKER','TAKER'):
 z=[r for r in primary if r['route']==rt];route[rt]={'n':len(z),'overflowMedian':med([r['overflow'] for r in z]),'overflowToRepairMedian':med([r['overflowToRepair'] for r in z]),'floorDeltaMedian':med([r['floorDelta'] for r in z]),'postFloorNonnegativeShare':sum(r['postFloorNonnegative'] for r in z)/len(z) if z else None}
qualified=[]
for a in axes:
 for o in outcomes:
  x=assoc[a][o];sp=x['spearman'];bs=x['medianSplit'];
  if sp is not None and abs(sp)>=.25 and bs is not None and bs['highN']>0 and bs['lowN']>0:qualified.append({'axis':a,'outcome':o,'spearman':sp,'highMinusLow':bs['highMinusLow']})
summary={'allCompositeParents':len(allrows),'pre180CompositeParents':len(primary),'marketsWithPre180Composite':sum(x['pre180']>0 for x in per),'overflowMedian':med([r['overflow'] for r in primary]),'repairMedian':med([r['repairAllocation'] for r in primary]),'overflowToRepairMedian':med([r['overflowToRepair'] for r in primary]),'overflowSpendToRepairGainMedian':med([r['overflowSpendToRepairGain'] for r in primary]),'overflowWithinFloorPreservingCapacityShare':sum((r['overflowToFloorPreservingCapacity'] or 1e9)<=1+1e-9 for r in primary)/len(primary) if primary else None,'postFloorNonnegativeShare':sum(r['postFloorNonnegative'] for r in primary)/len(primary) if primary else None,'floorDeltaPositiveShare':sum(r['floorDelta']>EPS for r in primary)/len(primary) if primary else None,'oldDebtGapMismatchMedian':med([r['oldDebtGapMismatch'] for r in primary]),'qualifiedAxes':qualified}
decision='KEEP_STRICT_PAST_STATE_PROPORTIONAL_BUDGET_AXIS_FOR_MICROWORLD' if qualified else 'REJECT_SIMPLE_STATE_PROPORTIONAL_COMPOSITE_SIZING_REQUIRE_BUDGET_OR_REGIME_HEAD'
res={'version':'TARGET_ETH_V85B_COMPOSITE_BUDGET_STATE_ANATOMY','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'primaryScope':'pre-180 Target Stage-A16 composite responsibility births','summary':summary,'associations':assoc,'routeGroups':route,'perMarket':per,'rows':primary,'decision':decision,'boundary':['Target post-market only','strict-past state at each physical parent','No Target raw qty/timing as runtime authority','No HFT behavior change','No tuning','V71F simple floor-credit conservation is not reinstated']}
OUT.write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'summary':summary,'routeGroups':route,'qualifiedAxes':qualified},ensure_ascii=False))
