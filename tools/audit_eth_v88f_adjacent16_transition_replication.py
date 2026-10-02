from __future__ import annotations
import json,sqlite3,math,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/target_wallet_official_v1.db';P=ROOT/'data/research/r4_v0/p0_provenance_v1';OUT=P/'TARGET_ETH_V88F_ADJACENT16_TRANSITION_REPLICATION_20260903.json';V88E=P/'TARGET_ETH_V88E_SMALL_OVERFLOW_RESPONSIBILITY_TRANSITION_20260903.json'
MIDS=[1828776,1828895,1829099,1829107,1829115,1829435,1829448,1829468,1829471,1829479,1829561,1829566,1829580,1829636,1830108,1830116];EPS=1e-9

def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return statistics.median(xs) if xs else None

def cliffs(a,b):
 a=[float(x) for x in a if x is not None and math.isfinite(float(x))];b=[float(x) for x in b if x is not None and math.isfinite(float(x))]
 if not a or not b:return None
 gt=lt=0
 for x in a:
  for y in b:
   if x>y:gt+=1
   elif x<y:lt+=1
 return (gt-lt)/(len(a)*len(b))

def summarize(rows,feat):
 c=[r[feat] for r in rows if r['nextParentComposite'] and r.get(feat) is not None];p=[r[feat] for r in rows if not r['nextParentComposite'] and r.get(feat) is not None]
 cm,pm=med(c),med(p)
 return {'compositeMedian':cm,'pureMedian':pm,'medianDiff':None if cm is None or pm is None else cm-pm,'cliffsDelta':cliffs(c,p)}

con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
ends={int(r['market_id']):int(r['window_end_ms']) for r in con.execute('select market_id,window_end_ms from target_markets where market_id in (%s)'%(','.join('?'*len(MIDS))),MIDS)}
all_parents={}
state_rows=[]
for mid in MIDS:
 rs=con.execute("select role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where market_id=? and asset='ETH' order by first_event_ms,parent_id",(mid,)).fetchall();all_parents[mid]=rs
 u=d=cost=0.0;cur=None
 for r in rs:
  t=int(r['first_event_ms']);p=float(r['average_price'] or 0);q=float(r['shares'] or 0);side=str(r['side']);route=str(r['role']);gap=abs(u-d);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;f0=min(u,d)-cost;b0=max(u,d)-cost
  repair=min(q,gap) if side==weak else 0.0;overflow=max(0.0,q-repair)
  old={'side':None,'debt':0.0,'ageSec':None,'expandPayments':0,'repairPayments':0,'expandQty':0.0,'repairPaid':0.0}
  if cur is not None:old={'side':cur['side'],'debt':cur['debt'],'ageSec':(t-cur['bornAt'])/1000.0,'expandPayments':cur['expandPayments'],'repairPayments':cur['repairPayments'],'expandQty':cur['expandQty'],'repairPaid':cur['repairPaid']}
  rg=repair*(1-p);osp=overflow*p;reserve=f0+rg
  if side=='UP':u+=q
  else:d+=q
  cost+=p*q;f1=min(u,d)-cost;b1=max(u,d)-cost
  if repair>EPS and cur is not None and side!=cur['side']:
   pay=min(repair,cur['debt']);cur['debt']-=pay;cur['repairPaid']+=pay;cur['repairPayments']+=1
  if overflow>EPS:
   if cur is None or side!=cur['side']:cur={'side':side,'bornAt':t,'debt':0.0,'expandPayments':0,'repairPayments':0,'expandQty':0.0,'repairPaid':0.0}
   cur['debt']+=overflow;cur['expandQty']+=overflow;cur['expandPayments']+=1
  elif repair<=EPS and q>EPS:
   if cur is None or side!=cur['side']:cur={'side':side,'bornAt':t,'debt':0.0,'expandPayments':0,'repairPayments':0,'expandQty':0.0,'repairPaid':0.0}
   cur['debt']+=q;cur['expandQty']+=q;cur['expandPayments']+=1
  if repair>EPS and overflow>EPS and t<=ends[mid]-180000:
   state_rows.append({'marketId':mid,'t':t,'parentId':str(r['parent_id']),'route':route,'side':side,'price':p,'repairGap':gap,'repairAllocation':repair,'overflow':overflow,'overflowToRepair':overflow/repair if repair>EPS else None,'floorBefore':f0,'floorAfter':f1,'floorDelta':f1-f0,'bestBefore':b0,'bestAfter':b1,'bestDelta':b1-b0,'repairFloorGain':rg,'overflowFloorSpend':osp,'overflowSpendToRepairGain':osp/rg if rg>EPS else None,'reserveAfterRepairBeforeOverflow':reserve,'oldResponsibilityAgeSec':old['ageSec'],'oldExpandPayments':old['expandPayments'],'oldRepairPayments':old['repairPayments'],'oldExpandQty':old['expandQty'],'oldRepairPaid':old['repairPaid']})
con.close()
by={(r['marketId'],r['t'],r['parentId']):r for r in state_rows};rows=[]
for mid,rs in all_parents.items():
 # reconstruct parent-level role decomposition by strict-past inventory again, preserving order
 u=d=0.0;parts=[]
 for r in rs:
  t=int(r['first_event_ms']);p=float(r['average_price'] or 0);q=float(r['shares'] or 0);side=str(r['side']);gap=abs(u-d);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;repair=min(q,gap) if side==weak else 0.0;overflow=max(0.0,q-repair)
  parts.append({'t':t,'pid':str(r['parent_id']),'side':side,'route':str(r['role']),'price':p,'repair':repair,'overflow':overflow})
  if side=='UP':u+=q
  else:d+=q
 for i,x in enumerate(parts):
  if x['repair']<=EPS or x['overflow']<=EPS or x['t']>ends[mid]-180000:continue
  s=by.get((mid,x['t'],x['pid']))
  if not s:continue
  opp='DOWN' if x['side']=='UP' else 'UP';nxt=None
  for y in parts[i+1:]:
   if y['t']<=x['t']:continue
   if y['side']==opp and y['repair']>EPS:
    legal=1.0/y['price'] if y['price']>EPS else None
    nxt={'lagSec':(y['t']-x['t'])/1000.0,'route':y['route'],'price':y['price'],'repair':y['repair'],'composite':y['overflow']>EPS,'legal':legal,'overflowCovers':bool(legal and x['overflow']+EPS>=legal)};break
  if not nxt or nxt['overflowCovers']:continue
  r=dict(s);r.update({'nextParentComposite':bool(nxt['composite']),'nextLagSec':nxt['lagSec'],'nextRoute':nxt['route'],'nextPrice':nxt['price'],'pairSum':x['price']+nxt['price'],'bestMinusFloorAfter':s['bestAfter']-s['floorAfter'],'oldPaidFrac':(s['oldRepairPaid']/s['oldExpandQty']) if s['oldExpandQty']>EPS else None});rows.append(r)
features=['bestDelta','bestBefore','oldExpandQty','floorAfter','overflowToRepair','oldResponsibilityAgeSec','pairSum','bestMinusFloorAfter','floorDelta','bestAfter','reserveAfterRepairBeforeOverflow']
stats={f:summarize(rows,f) for f in features};base=json.load(open(V88E,encoding='utf-8'))
rep={}
for f in ['bestDelta','bestBefore','oldExpandQty']:
 bd=base['featureStats'][f]['medianDiff'];rd=stats[f]['medianDiff'];bc=base['featureStats'][f]['cliffsDelta'];rc=stats[f]['cliffsDelta'];rep[f]={'baseMedianDiff':bd,'repMedianDiff':rd,'directionMatches':(bd is not None and rd is not None and bd*rd>0),'baseCliffs':bc,'repCliffs':rc,'cliffsDirectionMatches':(bc is not None and rc is not None and bc*rc>0)}
comp=sum(r['nextParentComposite'] for r in rows);pure=len(rows)-comp
kept=[f for f,x in rep.items() if x['directionMatches'] and x['cliffsDirectionMatches'] and x['repCliffs'] is not None and abs(x['repCliffs'])>=0.2]
decision='REPLICATE_RESPONSIBILITY_UPSIDE_STATE_AXIS' if len(kept)>=2 else 'DO_NOT_PROMOTE_V88E_AXIS_REQUIRE_MORE_STATE_OR_COHORT'
out={'version':'TARGET_ETH_V88F_ADJACENT16_TRANSITION_REPLICATION','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'fixedCohort':MIDS,'decision':decision,'summary':{'smallOverflowRows':len(rows),'nextComposite':comp,'nextPureRepair':pure,'nextCompositeShare':comp/len(rows) if rows else None,'replicatedPrimaryAxes':kept},'primaryReplication':rep,'featureStats':stats,'rows':rows,'boundary':['first 16 settled ETH markets immediately after Stage-A16 end','pre180 only','Target post-market only','no tuning','no runtime Target action/qty/timing']};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'summary':out['summary'],'primaryReplication':rep,'featureStats':{k:stats[k] for k in features[:7]}},ensure_ascii=False))
