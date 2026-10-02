from __future__ import annotations
import sqlite3,json,math,statistics,argparse
from collections import defaultdict
from pathlib import Path

def qtile(xs,q):
    if not xs:return None
    ys=sorted(xs); pos=(len(ys)-1)*q; lo=int(pos); hi=min(lo+1,len(ys)-1); a=pos-lo
    return ys[lo]*(1-a)+ys[hi]*a

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
    mr={int(r['market_id']):dict(r) for r in con.execute("select * from target_market_results where asset='ETH'")}
    by=defaultdict(list)
    for r in con.execute("select parent_id,market_id,role,side,first_event_ms,last_event_ms,average_price,shares,fill_legs from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"):
        by[int(r['market_id'])].append(dict(r))
    con.close()
    markets=[]; events=[]
    for mid,rows in by.items():
        u=d=cost=0.0; peak_floor=0.0; maxdd=0.0; risk=0.0; repair_credit=0.0; overspend=0.0; risk_events=0; overspend_events=0; floor_path=[]
        for i,r in enumerate(rows):
            p=float(r['average_price'] or 0); q=float(r['shares'] or 0); side=str(r['side']).upper(); t=int(r['first_event_ms'] or 0)
            if p<=0 or q<=0 or side not in ('UP','DOWN'): continue
            fb=min(u,d)-cost
            same=u if side=='UP' else d; opp=d if side=='UP' else u
            rq=min(q,max(0.0,opp-same)); eq=max(0.0,q-rq)
            rc=rq*(1.0-p); rs=eq*p
            reserve=max(0.0,fb)
            over=max(0.0,rs-reserve)
            if side=='UP':u+=q
            else:d+=q
            cost+=q*p
            fa=min(u,d)-cost
            peak_floor=max(peak_floor,fa); maxdd=max(maxdd,peak_floor-fa)
            floor_path.append((t,fa))
            risk+=rs; repair_credit+=rc; overspend+=over
            if eq>1e-12:
                risk_events+=1; overspend_events+=int(over>1e-12)
                events.append({'marketId':mid,'t':t,'role':r['role'],'side':side,'price':p,'qty':q,'repairQty':rq,'expandQty':eq,'floorBefore':fb,'floorAfter':fa,'repairCredit':rc,'riskSpend':rs,'positiveReserveBefore':reserve,'reserveOverspend':over})
        rec=mr.get(mid,{})
        pnl=float(rec.get('net_pnl_usdt') or 0.0)
        markets.append({'marketId':mid,'pnl':pnl,'winner':rec.get('winner'),'parentCount':len(rows),'totalRiskSpend':risk,'totalRepairCredit':repair_credit,'reserveOverspend':overspend,'riskEvents':risk_events,'overspendEvents':overspend_events,'terminalFloor':(floor_path[-1][1] if floor_path else 0.0),'maxFloorDrawdown':maxdd})
    # Attach strict-future recovery diagnostics to risk events using reconstructed floor paths per market.
    paths=defaultdict(list)
    for e in events: paths[e['marketId']].append(e)
    # Reconstruct full floor sequences again cheaply for recovery lookup.
    floorseq={}
    for mid,rows in by.items():
        u=d=cost=0.0; seq=[]
        for r in rows:
            p=float(r['average_price'] or 0); q=float(r['shares'] or 0); side=str(r['side']).upper(); t=int(r['first_event_ms'] or 0)
            if p<=0 or q<=0 or side not in ('UP','DOWN'):continue
            if side=='UP':u+=q
            else:d+=q
            cost+=q*p; seq.append((t,min(u,d)-cost))
        floorseq[mid]=seq
    for e in events:
        fut=[(t,f) for t,f in floorseq[e['marketId']] if t>e['t']]
        e['futureMaxFloor']=max((f for _,f in fut),default=e['floorAfter'])
        recov=[t for t,f in fut if f>=e['floorBefore']-1e-9]
        e['recoveredPriorFloor']=bool(recov)
        e['recoverySeconds']=(recov[0]-e['t'])/1000.0 if recov else None
    prof=[m for m in markets if m['pnl']>0]; loss=[m for m in markets if m['pnl']<=0]
    def ms(xs):
        return {'n':len(xs),'pnlSum':sum(m['pnl'] for m in xs),'pnlMedian':statistics.median([m['pnl'] for m in xs]) if xs else None,'riskSpendMedian':statistics.median([m['totalRiskSpend'] for m in xs]) if xs else None,'reserveOverspendMedian':statistics.median([m['reserveOverspend'] for m in xs]) if xs else None,'riskEventsMedian':statistics.median([m['riskEvents'] for m in xs]) if xs else None,'terminalFloorMedian':statistics.median([m['terminalFloor'] for m in xs]) if xs else None,'maxFloorDrawdownMedian':statistics.median([m['maxFloorDrawdown'] for m in xs]) if xs else None}
    re=[e for e in events if e['riskSpend']>0]; over=[e for e in re if e['reserveOverspend']>1e-12]
    out={'version':'TARGET_ETH_RISK_BUDGET_ANATOMY_V1','date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,'definition':{'repairCredit':'repairQty*(1-price)','expandRiskSpend':'expandQty*price','reserveOverspend':'max(0, expandRiskSpend-max(0,floorBefore))','note':'parent-average-price accounting approximation; fees/slippage not modeled'},'aggregate':{'markets':len(markets),'positiveMarkets':len(prof),'winRate':len(prof)/len(markets) if markets else 0,'pnlSum':sum(m['pnl'] for m in markets),'riskEvents':len(re),'overspendEvents':len(over),'overspendEventShare':len(over)/len(re) if re else 0,'riskSpend':sum(e['riskSpend'] for e in re),'reserveOverspend':sum(e['reserveOverspend'] for e in re),'reserveOverspendShareOfRiskSpend':sum(e['reserveOverspend'] for e in re)/sum(e['riskSpend'] for e in re) if sum(e['riskSpend'] for e in re)>0 else None,'riskEventRecoveredPriorFloorShare':sum(e['recoveredPriorFloor'] for e in re)/len(re) if re else None,'overspendRecoveredPriorFloorShare':sum(e['recoveredPriorFloor'] for e in over)/len(over) if over else None,'overspendRecoverySecondsMedian':statistics.median([e['recoverySeconds'] for e in over if e['recoverySeconds'] is not None]) if any(e['recoverySeconds'] is not None for e in over) else None,'overspendRecoverySecondsP90':qtile([e['recoverySeconds'] for e in over if e['recoverySeconds'] is not None],.9)},'profitableMarkets':ms(prof),'nonPositiveMarkets':ms(loss),'markets':markets,'riskEventsSample':events[:5000],'boundary':['offline Target post-market analysis only','winner/PnL never runtime input','no action authority','used to preregister OUR reserve-spend budgets, not copy Target raw scale']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'aggregate':out['aggregate'],'profitableMarkets':out['profitableMarkets'],'nonPositiveMarkets':out['nonPositiveMarkets']},ensure_ascii=False))
if __name__=='__main__': main()
