from __future__ import annotations
import json,sqlite3,math,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];DB=ROOT/'data/target_wallet_official_v1.db';P=ROOT/'data/research/r4_v0/p0_provenance_v1';OUT=P/'TARGET_ETH_V88G_EXECUTION_TRANCHE_VS_DEBT_20260903.json';EPS=1e-9
COHORTS={
 'stageA16':[1823603,1823769,1823894,1823897,1824037,1824747,1824852,1825353,1825959,1826030,1826386,1827223,1827418,1827903,1828268,1828768],
 'adjacent16':[1828776,1828895,1829099,1829107,1829115,1829435,1829448,1829468,1829471,1829479,1829561,1829566,1829580,1829636,1830108,1830116]
}

def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return statistics.median(xs) if xs else None

def mean(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return sum(xs)/len(xs) if xs else None

def cv(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 if len(xs)<2:return None
 m=mean(xs);return statistics.pstdev(xs)/abs(m) if m and abs(m)>EPS else None

def rank(a):
 idx=sorted(range(len(a)),key=lambda i:a[i]);r=[0.0]*len(a);i=0
 while i<len(idx):
  j=i+1
  while j<len(idx) and a[idx[j]]==a[idx[i]]:j+=1
  rr=(i+j-1)/2+1
  for k in range(i,j):r[idx[k]]=rr
  i=j
 return r

def pear(a,b):
 z=[(float(x),float(y)) for x,y in zip(a,b) if x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y))]
 if len(z)<4:return None
 x=[q[0] for q in z];y=[q[1] for q in z];mx=mean(x);my=mean(y);dx=[v-mx for v in x];dy=[v-my for v in y];den=(sum(v*v for v in dx)*sum(v*v for v in dy))**.5
 return sum(u*v for u,v in zip(dx,dy))/den if den>EPS else None

def spear(a,b):
 z=[(float(x),float(y)) for x,y in zip(a,b) if x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y))]
 if len(z)<4:return None
 return pear(rank([x for x,_ in z]),rank([y for _,y in z]))

def one(cohort):
 con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
 ph=','.join('?'*len(cohort));ends={int(r['market_id']):int(r['window_end_ms']) for r in con.execute(f'select market_id,window_end_ms from target_markets where market_id in ({ph})',cohort)}
 out=[]
 for mid in cohort:
  rs=con.execute("select role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where market_id=? and asset='ETH' order by first_event_ms,parent_id",(mid,)).fetchall();u=d=0.0;parts=[]
  for r in rs:
   t=int(r['first_event_ms']);p=float(r['average_price'] or 0);q=float(r['shares'] or 0);side=str(r['side']);gap=abs(u-d);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;repair=min(q,gap) if side==weak else 0.0;overflow=max(0.0,q-repair)
   parts.append({'t':t,'pid':str(r['parent_id']),'route':str(r['role']),'side':side,'price':p,'qty':q,'repair':repair,'overflow':overflow})
   if side=='UP':u+=q
   else:d+=q
  for i,x in enumerate(parts):
   if x['repair']<=EPS or x['overflow']<=EPS or x['t']>ends.get(mid,10**18)-180000:continue
   opp='DOWN' if x['side']=='UP' else 'UP';nxt=None
   for y in parts[i+1:]:
    if y['t']<=x['t']:continue
    if y['side']==opp and y['repair']>EPS:
     legal=1.0/y['price'] if y['price']>EPS else None
     nxt=y|{'legal':legal};break
   if nxt is None:continue
   small=bool(nxt['legal'] and x['overflow']+EPS<nxt['legal'])
   if not small:continue
   out.append({'marketId':mid,'t':x['t'],'previousRoute':x['route'],'previousPrice':x['price'],'previousOverflowDebt':x['overflow'],'nextLagSec':(nxt['t']-x['t'])/1000.0,'nextRoute':nxt['route'],'nextPrice':nxt['price'],'nextPhysicalQty':nxt['qty'],'nextRepairAllocation':nxt['repair'],'nextOverflowAllocation':nxt['overflow'],'nextParentComposite':nxt['overflow']>EPS,'nextLegalMinQty':nxt['legal'],'physicalQtyToDebt':nxt['qty']/x['overflow'] if x['overflow']>EPS else None,'repairAllocationToDebt':nxt['repair']/x['overflow'] if x['overflow']>EPS else None})
 con.close()
 def group(rows):
  return {'n':len(rows),'compositeShare':sum(r['nextParentComposite'] for r in rows)/len(rows) if rows else None,'physicalQtyMedian':med([r['nextPhysicalQty'] for r in rows]),'physicalQtyCV':cv([r['nextPhysicalQty'] for r in rows]),'repairAllocationMedian':med([r['nextRepairAllocation'] for r in rows]),'debtMedian':med([r['previousOverflowDebt'] for r in rows]),'physicalQtyToDebtMedian':med([r['physicalQtyToDebt'] for r in rows]),'repairAllocationToDebtMedian':med([r['repairAllocationToDebt'] for r in rows]),'exact10Share':sum(abs(r['nextPhysicalQty']-10)<=1e-6 for r in rows),'near5to10Share':sum(5-EPS<=r['nextPhysicalQty']<=10+EPS for r in rows)}
 byroute={rt:group([r for r in out if r['nextRoute']==rt]) for rt in ['MAKER','TAKER']}
 cor={'debtVsPhysicalQtySpearman':spear([r['previousOverflowDebt'] for r in out],[r['nextPhysicalQty'] for r in out]),'debtVsRepairAllocationSpearman':spear([r['previousOverflowDebt'] for r in out],[r['nextRepairAllocation'] for r in out]),'debtVsPhysicalQtyPearson':pear([r['previousOverflowDebt'] for r in out],[r['nextPhysicalQty'] for r in out]),'debtVsRepairAllocationPearson':pear([r['previousOverflowDebt'] for r in out],[r['nextRepairAllocation'] for r in out])}
 within={}
 for rt in ['MAKER','TAKER']:
  z=[r for r in out if r['nextRoute']==rt];within[rt]={'n':len(z),'debtVsPhysicalQtySpearman':spear([r['previousOverflowDebt'] for r in z],[r['nextPhysicalQty'] for r in z]),'debtVsRepairAllocationSpearman':spear([r['previousOverflowDebt'] for r in z],[r['nextRepairAllocation'] for r in z])}
 return {'summary':group(out),'routeGroups':byroute,'correlations':cor,'withinRouteCorrelations':within,'rows':out}

res={k:one(v) for k,v in COHORTS.items()}
# Replication interpretation: Repair allocation should track debt more strongly than total physical qty; route should show distinct tranche geometry in both cohorts.
checks={}
for name,x in res.items():
 c=x['correlations'];mk=x['routeGroups']['MAKER'];tk=x['routeGroups']['TAKER'];checks[name]={
  'repairTracksDebtAtLeastAsStrongAsPhysical': c['debtVsRepairAllocationSpearman'] is not None and c['debtVsPhysicalQtySpearman'] is not None and abs(c['debtVsRepairAllocationSpearman'])>=abs(c['debtVsPhysicalQtySpearman']),
  'makerPhysicalMedianAboveTaker': mk['physicalQtyMedian'] is not None and tk['physicalQtyMedian'] is not None and mk['physicalQtyMedian']>tk['physicalQtyMedian'],
  'makerHas10ShareConcentration': mk['n']>0 and mk['exact10Share']/mk['n']>=0.2
 }
keep=all(v['repairTracksDebtAtLeastAsStrongAsPhysical'] for v in checks.values()) and sum(v['makerPhysicalMedianAboveTaker'] for v in checks.values())>=1
decision='KEEP_EXECUTION_TRANCHE_ALLOCATION_INTERPRETATION' if keep else 'MIXED_EVIDENCE_CONTINUE_EXECUTION_TRANCHE_ANATOMY'
out={'version':'TARGET_ETH_V88G_EXECUTION_TRANCHE_VS_DEBT','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'decision':decision,'checks':checks,'cohorts':res,'interpretationBoundary':['Small-overflow predecessor only','Target post-market actual parent fills','Physical qty is descriptive execution geometry, never copied to OUR runtime','No controller behavior change']};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'checks':checks,'stage':{k:v for k,v in res['stageA16'].items() if k!='rows'},'adjacent':{k:v for k,v in res['adjacent16'].items() if k!='rows'}},ensure_ascii=False))
