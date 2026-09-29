from __future__ import annotations
import sqlite3,json,statistics
from pathlib import Path
from predict_bot.core import taker_fee
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'r4_v0'/'r4_first_safe_survival_v1.json'
FEE_BPS=200; H=15000; TOL=-5.0
c=sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1",uri=True); c.row_factory=sqlite3.Row
res={int(r['market_id']):dict(r) for r in c.execute("select market_id,winner,net_pnl_usdt from target_market_results where asset='BTC'")}
rows=c.execute("select market_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='BTC' order by market_id,first_event_ms,parent_id")

def med(xs): xs=[x for x in xs if x is not None]; return statistics.median(xs) if xs else None
markets=[]; cur=None; evs=[]
def finish(mid,evs):
    if mid not in res or not evs:return
    up=dn=cost=fees=0.; tr=[]; pf=pu=0.
    for i,e in enumerate(evs):
        sh=float(e['shares'] or 0); px=float(e['average_price'] or 0); role=str(e['role']); side=str(e['side']); t=int(e['first_event_ms'] or 0)
        pre='UP' if up>dn else 'DOWN' if dn>up else 'FLAT'
        if side=='UP':up+=sh
        elif side=='DOWN':dn+=sh
        cost+=sh*px
        if role=='TAKER':fees+=float(taker_fee(sh,px,FEE_BPS))
        piu=up-cost-fees; pid=dn-cost-fees; floor=min(piu,pid); upside=max(piu,pid)
        structural='FLAT_START' if pre=='FLAT' else ('ADD' if side==pre else 'REPAIR')
        tr.append(dict(i=i,t=t,role=role,side=side,shares=sh,price=px,floor=floor,upside=upside,dFloor=floor-pf,dUpside=upside-pu,structural=structural,surplus=abs(up-dn),base=min(up,dn)))
        pf,pu=floor,upside
    fs=next((x for x in tr if x['floor']>=0),None)
    if not fs:return
    w15=[x for x in tr if fs['t']<=x['t']<=fs['t']+15000]
    durable=bool(w15 and min(x['floor'] for x in w15)>=TOL and w15[-1]['floor']>=0)
    def post(ms):
        w=[x for x in tr if fs['t']<x['t']<=fs['t']+ms]
        return dict(
          events=len(w), makerRepairShares=sum(x['shares'] for x in w if x['role']=='MAKER' and x['structural']=='REPAIR'),
          makerAddShares=sum(x['shares'] for x in w if x['role']=='MAKER' and x['structural']=='ADD'),
          takerRepairShares=sum(x['shares'] for x in w if x['role']=='TAKER' and x['structural']=='REPAIR'),
          takerAddShares=sum(x['shares'] for x in w if x['role']=='TAKER' and x['structural']=='ADD'),
          makerRepairDFloor=sum(x['dFloor'] for x in w if x['role']=='MAKER' and x['structural']=='REPAIR'),
          makerAddDFloor=sum(x['dFloor'] for x in w if x['role']=='MAKER' and x['structural']=='ADD'),
          takerRepairDFloor=sum(x['dFloor'] for x in w if x['role']=='TAKER' and x['structural']=='REPAIR'),
          takerAddDFloor=sum(x['dFloor'] for x in w if x['role']=='TAKER' and x['structural']=='ADD'),
          endFloor=(w[-1]['floor'] if w else fs['floor']), endUpside=(w[-1]['upside'] if w else fs['upside']), endSurplus=(w[-1]['surplus'] if w else fs['surplus']),
          minFloor=min([fs['floor']]+[x['floor'] for x in w])
        )
    markets.append(dict(marketId=mid,durable=durable,firstSafe=fs,post15=post(15000),post30=post(30000),officialPnl=res[mid].get('net_pnl_usdt')))
for r in rows:
    mid=int(r['market_id'])
    if cur is None:cur=mid
    if mid!=cur:finish(cur,evs);cur=mid;evs=[]
    evs.append(r)
finish(cur,evs)

def group(ms):
    def m(p,k):return med([x[p][k] for x in ms])
    return dict(markets=len(ms),positiveRate=sum((x['officialPnl'] or 0)>0 for x in ms)/len(ms) if ms else None,
      firstSafeFloor=med([x['firstSafe']['floor'] for x in ms]),firstSafeSurplus=med([x['firstSafe']['surplus'] for x in ms]),
      post15MinFloor=m('post15','minFloor'),post15EndFloor=m('post15','endFloor'),post15EndSurplus=m('post15','endSurplus'),
      post15MakerRepairShares=m('post15','makerRepairShares'),post15MakerAddShares=m('post15','makerAddShares'),post15TakerRepairShares=m('post15','takerRepairShares'),post15TakerAddShares=m('post15','takerAddShares'),
      post15MakerRepairDFloor=m('post15','makerRepairDFloor'),post15MakerAddDFloor=m('post15','makerAddDFloor'),post15TakerRepairDFloor=m('post15','takerRepairDFloor'),post15TakerAddDFloor=m('post15','takerAddDFloor'),
      post30MinFloor=m('post30','minFloor'),post30EndFloor=m('post30','endFloor'),post30EndSurplus=m('post30','endSurplus'))
dur=[x for x in markets if x['durable']]; fra=[x for x in markets if not x['durable']]
out={'version':'R4_FIRST_SAFE_SURVIVAL_V1','boundary':'Target official parent-order accounting. Compare post-first-safe realized path; no thresholds tuned. Durable uses fixed V0 15s/-5 definition.','summary':{'everSafeMarkets':len(markets),'durable':group(dur),'fragile':group(fra)},'markets':markets}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'out':str(OUT),'summary':out['summary']},ensure_ascii=False,indent=2))