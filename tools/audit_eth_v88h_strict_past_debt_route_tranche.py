from __future__ import annotations
import json,sqlite3,math,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];DB=ROOT/'data/target_wallet_official_v1.db';P=ROOT/'data/research/r4_v0/p0_provenance_v1';OUT=P/'TARGET_ETH_V88H_STRICT_PAST_DEBT_ROUTE_TRANCHE_20260903.json';EPS=1e-9
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
 x=[v[0] for v in z];y=[v[1] for v in z];mx=mean(x);my=mean(y);dx=[v-mx for v in x];dy=[v-my for v in y];den=(sum(v*v for v in dx)*sum(v*v for v in dy))**.5
 return sum(a*b for a,b in zip(dx,dy))/den if den>EPS else None

def spear(a,b):
 z=[(float(x),float(y)) for x,y in zip(a,b) if x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y))]
 if len(z)<4:return None
 return pear(rank([x for x,_ in z]),rank([y for _,y in z]))

def build(cohort):
 con=sqlite3.connect(DB);con.row_factory=sqlite3.Row;ph=','.join('?'*len(cohort));ends={int(r['market_id']):int(r['window_end_ms']) for r in con.execute(f'select market_id,window_end_ms from target_markets where market_id in ({ph})',cohort)}
 markets={};allRepair=[]
 for mid in cohort:
  rs=con.execute("select role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where market_id=? and asset='ETH' order by first_event_ms,parent_id",(mid,)).fetchall();u=d=0.0;parts=[]
  for r in rs:
   t=int(r['first_event_ms']);p=float(r['average_price'] or 0);q=float(r['shares'] or 0);side=str(r['side']);gap=abs(u-d);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;repair=min(q,gap) if side==weak else 0.0;overflow=max(0.0,q-repair);pre180=t<=ends.get(mid,10**18)-180000
   part={'marketId':mid,'t':t,'pid':str(r['parent_id']),'route':str(r['role']),'side':side,'price':p,'qty':q,'gapBefore':gap,'weakBefore':weak,'repair':repair,'overflow':overflow,'pre180':pre180}
   parts.append(part)
   if repair>EPS and pre180:allRepair.append(part)
   if side=='UP':u+=q
   else:d+=q
  markets[mid]=parts
 con.close()
 small=[]
 for mid,parts in markets.items():
  for i,x in enumerate(parts):
   if not x['pre180'] or x['repair']<=EPS or x['overflow']<=EPS:continue
   opp='DOWN' if x['side']=='UP' else 'UP';nxt=None
   for y in parts[i+1:]:
    if y['t']<=x['t']:continue
    if y['side']==opp and y['repair']>EPS:
     nxt=y;break
   if nxt is None:continue
   legal=1.0/nxt['price'] if nxt['price']>EPS else None
   if not legal or x['overflow']+EPS>=legal:continue
   debt=float(nxt['gapBefore']);small.append({'marketId':mid,'prevT':x['t'],'prevOverflowAtBirth':x['overflow'],'nextT':nxt['t'],'nextRoute':nxt['route'],'nextPrice':nxt['price'],'debtAtNextStrictPast':debt,'nextPhysicalQty':nxt['qty'],'nextRepairAllocation':nxt['repair'],'nextOverflowAllocation':nxt['overflow'],'nextParentComposite':nxt['overflow']>EPS,'repairToDebt':nxt['repair']/debt if debt>EPS else None,'physicalToDebt':nxt['qty']/debt if debt>EPS else None,'interveningDebtGrowth':debt-x['overflow']})
 routeRef={}
 for rt in ['MAKER','TAKER']:
  z=[r for r in allRepair if r['route']==rt];routeRef[rt]={'n':len(z),'physicalQtyMedian':med([r['qty'] for r in z]),'physicalQtyCV':cv([r['qty'] for r in z]),'exact10Share':sum(abs(r['qty']-10)<=1e-6 for r in z),'exact10ShareFrac':sum(abs(r['qty']-10)<=1e-6 for r in z)/len(z) if z else None}
 cor={'debtVsPhysicalSpearman':spear([r['debtAtNextStrictPast'] for r in small],[r['nextPhysicalQty'] for r in small]),'debtVsRepairSpearman':spear([r['debtAtNextStrictPast'] for r in small],[r['nextRepairAllocation'] for r in small]),'repairToDebtMedian':med([r['repairToDebt'] for r in small]),'interveningDebtGrowthMedian':med([r['interveningDebtGrowth'] for r in small])}
 return {'rows':small,'routeReference':routeRef,'correlations':cor,'summary':{'n':len(small),'compositeShare':sum(r['nextParentComposite'] for r in small)/len(small) if small else None,'debtMedian':med([r['debtAtNextStrictPast'] for r in small]),'physicalMedian':med([r['nextPhysicalQty'] for r in small]),'repairMedian':med([r['nextRepairAllocation'] for r in small])}}

def predict(rows,ref):
 tp=tn=fp=fn=0
 for r in rows:
  q=ref.get(r['nextRoute'],{}).get('physicalQtyMedian')
  if q is None:continue
  pred=q>r['debtAtNextStrictPast']+EPS;y=r['nextParentComposite']
  if pred and y:tp+=1
  elif pred and not y:fp+=1
  elif not pred and not y:tn+=1
  else:fn+=1
 n=tp+tn+fp+fn;acc=(tp+tn)/n if n else None;tpr=tp/(tp+fn) if tp+fn else None;tnr=tn/(tn+fp) if tn+fp else None;ba=(tpr+tnr)/2 if tpr is not None and tnr is not None else None
 return {'n':n,'accuracy':acc,'balancedAccuracy':ba,'tp':tp,'tn':tn,'fp':fp,'fn':fn}
res={k:build(v) for k,v in COHORTS.items()};cross={'stageRefPredictAdjacent':predict(res['adjacent16']['rows'],res['stageA16']['routeReference']),'adjacentRefPredictStage':predict(res['stageA16']['rows'],res['adjacent16']['routeReference'])}
# Key architecture evidence: actual Repair allocation should equal/cap at current debt, while route-reference tranche has cross-cohort predictive value for crossing.
checks={k:{'repairAllocationTracksStrictPastDebt':v['correlations']['repairToDebtMedian'] is not None and abs(v['correlations']['repairToDebtMedian']-1)<=0.05,'maker10Concentration':v['routeReference']['MAKER']['exact10ShareFrac'] is not None and v['routeReference']['MAKER']['exact10ShareFrac']>=0.2} for k,v in res.items()}
keep=all(x['repairAllocationTracksStrictPastDebt'] for x in checks.values()) and all(x['maker10Concentration'] for x in checks.values()) and any((x.get('balancedAccuracy') or 0)>=0.6 for x in cross.values())
decision='KEEP_MANAGER_DEBT_EXECUTION_TRANCHE_SEPARATION_FOR_MICROWORLD' if keep else 'MIXED_STRICT_PAST_DEBT_TRANCHE_EVIDENCE'
out={'version':'TARGET_ETH_V88H_STRICT_PAST_DEBT_ROUTE_TRANCHE','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'decision':decision,'checks':checks,'crossCohortRouteMedianPrediction':cross,'cohorts':res,'boundary':['Correct debt is strict-past gap immediately before next Repair parent','Target post-market only','Route median prediction is architecture diagnostic, not runtime threshold/size','No Target qty copied to OUR runtime']};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'checks':checks,'cross':cross,'stage':{k:v for k,v in res['stageA16'].items() if k!='rows'},'adjacent':{k:v for k,v in res['adjacent16'].items() if k!='rows'}},ensure_ascii=False))
