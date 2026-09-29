from __future__ import annotations
import sqlite3, json, statistics
from pathlib import Path
from predict_bot.core import taker_fee

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'r4_v0'/'r4_durable_locked_base_formation_v0.json'
OUT.parent.mkdir(parents=True, exist_ok=True)
FEE_BPS=200
# Preregistered diagnostic definition, not tuned: first floor>=0 whose next 15s path never falls below -5 USDT
# and whose last observed point within that 15s window is >=0. If market ends earlier, use available remainder.
DURABLE_HORIZON_MS=15000
TOLERANCE_FLOOR=-5.0

c=sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1",uri=True); c.row_factory=sqlite3.Row
results=c.execute("select market_id,winner,net_pnl_usdt,resolved_at_ms from target_market_results where asset='BTC' order by resolved_at_ms").fetchall()
res={int(r['market_id']):dict(r) for r in results}
rows=c.execute("select market_id,role,side,average_price,shares,first_event_ms,last_event_ms from target_parent_orders where asset='BTC' order by market_id,first_event_ms,parent_id")

markets=[]; cur=None; evs=[]
def median(xs):
    xs=[x for x in xs if x is not None]
    return statistics.median(xs) if xs else None

def build(mid, evs):
    if mid not in res or not evs: return
    up=down=cost=fees=0.0; tr=[]
    prev_floor=prev_upside=0.0
    for i,e in enumerate(evs):
        sh=float(e['shares'] or 0); px=float(e['average_price'] or 0); role=str(e['role']); side=str(e['side']); t=int(e['first_event_ms'] or 0)
        pre_up,pre_down=up,down
        pre_surplus_side='UP' if pre_up>pre_down else 'DOWN' if pre_down>pre_up else 'FLAT'
        if side=='UP': up+=sh
        elif side=='DOWN': down+=sh
        cost += sh*px
        if role=='TAKER': fees += float(taker_fee(sh,px,FEE_BPS))
        pu=up-cost-fees; pd=down-cost-fees; floor=min(pu,pd); upside=max(pu,pd)
        post_surplus_side='UP' if up>down else 'DOWN' if down>up else 'FLAT'
        # structural event label uses strictly pre-event inventory relation
        if pre_surplus_side=='FLAT': structural='FLAT_START'
        elif side==pre_surplus_side: structural='ADD'
        else: structural='REPAIR'
        tr.append(dict(i=i,t=t,role=role,side=side,price=px,shares=sh,upShares=up,downShares=down,
                       floor=floor,upside=upside,dFloor=floor-prev_floor,dUpside=upside-prev_upside,
                       surplusShares=abs(up-down),basePairShares=min(up,down),preSurplusSide=pre_surplus_side,
                       postSurplusSide=post_surplus_side,structural=structural))
        prev_floor,prev_upside=floor,upside
    first_safe=next((x for x in tr if x['floor']>=0),None)
    durable=None
    for x in tr:
        if x['floor']<0: continue
        w=[y for y in tr if x['t']<=y['t']<=x['t']+DURABLE_HORIZON_MS]
        if w and min(y['floor'] for y in w)>=TOLERANCE_FLOOR and w[-1]['floor']>=0:
            durable=x; break
    def window_stats(anchor,ms):
        if not anchor:return None
        w=[x for x in tr if anchor['t']-ms<=x['t']<=anchor['t']]
        weakmaker=[x for x in w if x['role']=='MAKER' and x['structural']=='REPAIR']
        strongmaker=[x for x in w if x['role']=='MAKER' and x['structural']=='ADD']
        takerrepair=[x for x in w if x['role']=='TAKER' and x['structural']=='REPAIR']
        return {
          'events':len(w),'makerEvents':sum(x['role']=='MAKER' for x in w),'takerEvents':sum(x['role']=='TAKER' for x in w),
          'makerRepairShares':sum(x['shares'] for x in weakmaker),'makerAddShares':sum(x['shares'] for x in strongmaker),
          'takerRepairShares':sum(x['shares'] for x in takerrepair),
          'makerRepairDFloor':sum(x['dFloor'] for x in weakmaker),'makerRepairDUpside':sum(x['dUpside'] for x in weakmaker),
          'makerAddDFloor':sum(x['dFloor'] for x in strongmaker),'makerAddDUpside':sum(x['dUpside'] for x in strongmaker),
          'takerRepairDFloor':sum(x['dFloor'] for x in takerrepair),'takerRepairDUpside':sum(x['dUpside'] for x in takerrepair),
          'startFloor':w[0]['floor'] if w else None,'endFloor':anchor['floor'],'startSurplus':w[0]['surplusShares'] if w else None,'endSurplus':anchor['surplusShares']
        }
    info=res[mid]
    markets.append({'marketId':mid,'winner':info.get('winner'),'officialNetPnl':info.get('net_pnl_usdt'),
                    'parents':len(tr),'firstSafe':first_safe,'firstDurable':durable,
                    'w15':window_stats(durable,15000),'w30':window_stats(durable,30000),'w60':window_stats(durable,60000),
                    'terminal':tr[-1]})

for r in rows:
    mid=int(r['market_id'])
    if cur is None:cur=mid
    if mid!=cur:
        build(cur,evs); cur=mid; evs=[]
    evs.append(r)
build(cur,evs)

ever=[m for m in markets if m['firstSafe']]
durable=[m for m in markets if m['firstDurable']]
fragile=[m for m in markets if m['firstSafe'] and not m['firstDurable']]
never_safe=[m for m in markets if not m['firstSafe']]

def group(ms):
    def g(path,key):
        vals=[]
        for m in ms:
            x=m.get(path)
            if x and x.get(key) is not None: vals.append(x[key])
        return median(vals)
    return {
      'markets':len(ms),
      'officialPositiveRate':sum((m['officialNetPnl'] or 0)>0 for m in ms)/len(ms) if ms else None,
      'medianOfficialPnl':median([m['officialNetPnl'] for m in ms]),
      'medianFirstSafeFloor':median([m['firstSafe']['floor'] for m in ms if m['firstSafe']]),
      'medianDurableFloor':median([m['firstDurable']['floor'] for m in ms if m['firstDurable']]),
      'medianDurableSurplus':median([m['firstDurable']['surplusShares'] for m in ms if m['firstDurable']]),
      'w30MedianMakerRepairShares':g('w30','makerRepairShares'),
      'w30MedianMakerAddShares':g('w30','makerAddShares'),
      'w30MedianTakerRepairShares':g('w30','takerRepairShares'),
      'w30MedianMakerRepairDFloor':g('w30','makerRepairDFloor'),
      'w30MedianMakerRepairDUpside':g('w30','makerRepairDUpside'),
      'w30MedianMakerAddDFloor':g('w30','makerAddDFloor'),
      'w30MedianMakerAddDUpside':g('w30','makerAddDUpside'),
      'w30MedianTakerRepairDFloor':g('w30','takerRepairDFloor'),
      'w30MedianFloorGain': median([(m['w30']['endFloor']-m['w30']['startFloor']) for m in ms if m.get('w30')]),
      'w30MedianSurplusChange': median([(m['w30']['endSurplus']-m['w30']['startSurplus']) for m in ms if m.get('w30')]),
    }
summary={
 'markets':len(markets),'everSafe':len(ever),'everSafeRate':len(ever)/len(markets) if markets else None,
 'durableLockedBase':len(durable),'durableLockedBaseRate':len(durable)/len(markets) if markets else None,
 'durableGivenEverSafeRate':len(durable)/len(ever) if ever else None,
 'fragileSafeOnly':len(fragile),'neverSafe':len(never_safe),
 'durable':group(durable),'fragile':group(fragile),'neverSafeGroup':group(never_safe)
}
out={'version':'R4_DURABLE_LOCKED_BASE_FORMATION_V0','boundary':'Target official BID parent-order accounting only; no winner/future feature used in formation detection. This is descriptive decomposition, not intent proof.',
     'definition':{'durableHorizonMs':DURABLE_HORIZON_MS,'floorToleranceUsdt':TOLERANCE_FLOOR,'rule':'first floor>=0 checkpoint whose observed next-15s floor never drops below -5 and whose last observed checkpoint in that window is >=0; fixed diagnostic definition, not tuned'},
     'summary':summary,'markets':markets}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'out':str(OUT),'summary':summary},ensure_ascii=False,indent=2))
