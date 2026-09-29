from __future__ import annotations
import sqlite3,statistics,math,datetime,zoneinfo,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data'/'research'/'r4_v0'/'r4_target_crossing_fill_cadence_v1.json'
c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
res={}
for r in c.execute("select market_id,resolved_at_ms from target_market_results where asset='BTC'"):
 t=int(r['resolved_at_ms'] or 0)
 if not t: continue
 dt=datetime.datetime.fromtimestamp(t/1000,datetime.timezone.utc).astimezone(zoneinfo.ZoneInfo('Asia/Taipei'))
 if dt.date()==datetime.date(2026,8,16): continue
 res[int(r['market_id'])]=t
parents={}
for r in c.execute("select market_id,role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where asset='BTC' order by market_id,first_event_ms,parent_id"):
 mid=int(r['market_id'])
 if mid in res: parents.setdefault(mid,[]).append(dict(r))
def fee(sh,px): return sh*px*(1-px)*.02*4
def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]; return statistics.median(xs) if xs else None
anchors=[]
for mid,evs in parents.items():
 up=down=cost=fees=0.0; tr=[]
 for i,e in enumerate(evs):
  sh=float(e['shares'] or 0); px=float(e['average_price'] or 0); side=e['side']; role=e['role']; t=int(e['first_event_ms'] or 0)
  if side=='UP': up+=sh
  elif side=='DOWN': down+=sh
  cost+=sh*px
  if role=='TAKER': fees+=fee(sh,px)
  tr.append((i,t,min(up-cost-fees,down-cost-fees),abs(up-down),up,down))
 fs=next((x for x in tr if x[2]>=0),None)
 if not fs or fs[0]==0: continue
 post=[x for x in tr if fs[1]<=x[1]<=fs[1]+15000]
 durable=bool(post and min(x[2] for x in post)>=-5 and post[-1][2]>=0)
 pre=tr[fs[0]-1]
 ss='UP' if pre[4]>pre[5] else 'DOWN' if pre[5]>pre[4] else 'FLAT'; weak='DOWN' if ss=='UP' else 'UP' if ss=='DOWN' else 'FLAT'
 anchors.append((mid,pre[1]-15000,pre[1],int(durable),ss,weak,pre[2],pre[3],fs[2],fs[3]))
c.execute('create temp table a(mid integer primary key,t0 integer,t1 integer,durable integer,ss text,weak text,pref real,pres real,cf real,cs real)')
c.executemany('insert into a values(?,?,?,?,?,?,?,?,?,?)',anchors)
rows=c.execute("select e.market_id,e.side,e.event_ms,e.observed_at_ms,e.price,e.shares,a.durable,a.ss,a.weak,a.pref,a.pres,a.cf,a.cs from wallet_shadow_target_events e join a on a.mid=e.market_id and e.event_ms between a.t0 and a.t1 where e.asset='BTC' and e.role='MAKER' order by e.market_id,e.event_ms,e.id").fetchall()
by={}
for r in rows: by.setdefault(int(r['market_id']),[]).append(r)
out=[]
for a in anchors:
 mid,_,_,dur,ss,weak,pref,pres,cf,cs=a; L=by.get(mid,[]); W=[r for r in L if r['side']==weak]; S=[r for r in L if r['side']==ss]
 def cad(A):
  ts=[int(r['event_ms']) for r in A]; gaps=[(b-a)/1000 for a,b in zip(ts,ts[1:])]; px=[float(r['price']) for r in A]; sh=[float(r['shares']) for r in A]; lag=[int(r['observed_at_ms'] or r['event_ms'])-int(r['event_ms']) for r in A]
  return {'n':len(A),'shares':sum(sh),'medGapS':med(gaps),'medSize':med(sh),'priceRange':(max(px)-min(px)) if px else None,'medObservedLagMs':med(lag)}
 out.append({'marketId':mid,'durable':dur,'preFloor':pref,'preSurplus':pres,'crossFloor':cf,'crossSurplus':cs,'weak':cad(W),'surplus':cad(S)})
def grp(flag):
 g=[x for x in out if x['durable']==flag]
 def M(side,key): return med([x[side][key] for x in g if x[side][key] is not None])
 return {'markets':len(g),'preFloor':med([x['preFloor'] for x in g]),'preSurplus':med([x['preSurplus'] for x in g]),'crossFloor':med([x['crossFloor'] for x in g]),'crossSurplus':med([x['crossSurplus'] for x in g]),'weakMakerLegs15s':M('weak','n'),'weakMakerShares15s':M('weak','shares'),'weakMedianInterfillGapS':M('weak','medGapS'),'weakMedianFillSize':M('weak','medSize'),'weakPriceRange15s':M('weak','priceRange'),'weakMedianObservedLagMs':M('weak','medObservedLagMs'),'surplusMakerLegs15s':M('surplus','n'),'surplusMakerShares15s':M('surplus','shares'),'surplusMedianInterfillGapS':M('surplus','medGapS')}
report={'version':'R4_TARGET_CROSSING_FILL_CADENCE_V1','definition':'Ordinary Target markets excluding Taipei 2026-08-16. Compare realized Maker fill-leg cadence in 15s before first floor>=0 crossing, durable vs fragile crossing. Descriptive only; no winner/runtime future feature.','durable':grp(1),'fragile':grp(0)}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2))
