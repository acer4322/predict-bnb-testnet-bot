from __future__ import annotations
import sqlite3, json, math, statistics
from pathlib import Path
from predict_bot.core import taker_fee
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'r3_v0'/'target_payoff_trajectory_v0.json'
FEE_BPS=200
uri=f"file:{DB.as_posix()}?mode=ro&immutable=1"
c=sqlite3.connect(uri,uri=True); c.row_factory=sqlite3.Row
results=c.execute("select market_id,winner,net_pnl_usdt,resolved_at_ms from target_market_results where asset='BTC' order by resolved_at_ms").fetchall()
market_ids=[int(r['market_id']) for r in results]
resmap={int(r['market_id']):dict(r) for r in results}
# stream all BTC parent orders ordered by market/time; immutable read avoids writer lock
rows=c.execute("select market_id,role,side,average_price,shares,first_event_ms,last_event_ms from target_parent_orders where asset='BTC' order by market_id,first_event_ms,parent_id")
markets=[]; cur=None; evs=[]
def finish(mid,evs):
    if mid is None or not evs:return
    up=down=cost=fees=0.0; traj=[]
    for i,e in enumerate(evs):
        sh=float(e['shares'] or 0); px=float(e['average_price'] or 0); role=str(e['role']); side=str(e['side'])
        if side=='UP': up+=sh
        elif side=='DOWN': down+=sh
        cost+=sh*px
        if role=='TAKER': fees+=float(taker_fee(sh,px,FEE_BPS))
        pup=up-cost-fees; pdn=down-cost-fees; floor=min(pup,pdn); upside=max(pup,pdn)
        traj.append({'i':i,'t':int(e['first_event_ms'] or 0),'role':role,'side':side,'price':px,'shares':sh,'upShares':up,'downShares':down,'cost':cost,'fees':fees,'pnlIfUp':pup,'pnlIfDown':pdn,'floor':floor,'upside':upside,'surplusSide':'UP' if up>down else 'DOWN' if down>up else 'FLAT','surplusShares':abs(up-down),'basePairShares':min(up,down)})
    terminal=traj[-1]; info=resmap.get(mid,{})
    # strongest state where one outcome has meaningful upside and the opposite floor is nonnegative
    safe=[x for x in traj if x['floor']>=-1e-9]
    best_safe=max(safe,key=lambda x:x['upside']) if safe else None
    # detect periods where upside increased >=25 while floor stayed >= -5 from an earlier safe-ish checkpoint
    max_safe_up=max((x['upside'] for x in traj if x['floor']>=-5),default=None)
    markets.append({'marketId':mid,'winner':info.get('winner'),'officialNetPnl':info.get('net_pnl_usdt'),'parents':len(evs),'terminal':terminal,'bestNonnegativeFloor':best_safe,'maxUpsideWithFloorGeMinus5':max_safe_up})
for r in rows:
    mid=int(r['market_id'])
    if cur is None: cur=mid
    if mid!=cur:
        finish(cur,evs); cur=mid; evs=[]
    evs.append(r)
finish(cur,evs)
# restrict to markets with official result
markets=[m for m in markets if m['marketId'] in resmap]
# summary
term=[m['terminal'] for m in markets]
safe_terminal=[m for m in markets if m['terminal']['floor']>=0]
positive_off=[m for m in markets if (m['officialNetPnl'] or 0)>0]
hyp=[m for m in markets if m['bestNonnegativeFloor'] and m['bestNonnegativeFloor']['upside']>=25]
# among official winners, is winning-outcome payoff the high side at terminal
align=[]
for m in markets:
    w=m['winner']; t=m['terminal']
    if w=='UP': align.append(t['pnlIfUp']>=t['pnlIfDown'])
    elif w=='DOWN': align.append(t['pnlIfDown']>=t['pnlIfUp'])
# top examples by safe upside
examples=sorted([m for m in markets if m['bestNonnegativeFloor']], key=lambda m:m['bestNonnegativeFloor']['upside'], reverse=True)[:20]
summary={
 'markets':len(markets),
 'terminalFloorGe0':len(safe_terminal),
 'terminalFloorGe0Rate':len(safe_terminal)/len(markets) if markets else None,
 'officialPositiveMarkets':len(positive_off),
 'officialPositiveRate':len(positive_off)/len(markets) if markets else None,
 'everUpsideGe25WhileFloorGe0':len(hyp),
 'everUpsideGe25WhileFloorGe0Rate':len(hyp)/len(markets) if markets else None,
 'medianTerminalFloor':statistics.median([x['floor'] for x in term]),
 'medianTerminalUpside':statistics.median([x['upside'] for x in term]),
 'p90TerminalUpside':sorted([x['upside'] for x in term])[int(.9*(len(term)-1))],
 'winnerMatchesHigherTerminalPayoffRate':sum(align)/len(align) if align else None,
}
OUT.write_text(json.dumps({'version':'TARGET_R3_PAYOFF_TRAJECTORY_V0','feeBps':FEE_BPS,'boundary':'Uses official target_parent_orders BID fills only; reconstructed two-outcome payoff after each parent. This is accounting reconstruction, not proof of intent.','summary':summary,'topSafeUpsideExamples':examples,'markets':markets},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'out':str(OUT),'summary':summary,'examples':[{'m':m['marketId'],'winner':m['winner'],'official':m['officialNetPnl'],'best':m['bestNonnegativeFloor']} for m in examples[:8]]},ensure_ascii=False,indent=2))
