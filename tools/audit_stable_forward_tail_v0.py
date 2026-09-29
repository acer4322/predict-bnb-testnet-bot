from __future__ import annotations
import json, sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FLASH=ROOT/'data'/'strategy_target_flash_v1.db'
BASE=ROOT/'data'/'strategy_target_compare_v1.db'
TARGET=ROOT/'data'/'target_wallet_official_v1.db'
VERSION='FLASH:STABLE_DIRECTIONAL_TOLERANCE_V1:R1'
MARKET=1483435

def ro(p):
    c=sqlite3.connect(f'file:{p.resolve()}?mode=ro',uri=True); c.row_factory=sqlite3.Row; return c

def fifo_pair(fills):
    qs={'UP':[],'DOWN':[]}
    for f in fills:
        qs[f['side']].append([float(f['shares']),float(f['price'])])
    i=j=0; paired=edge=0.0
    while i<len(qs['UP']) and j<len(qs['DOWN']):
        q=min(qs['UP'][i][0],qs['DOWN'][j][0])
        paired+=q; edge+=q*(1-qs['UP'][i][1]-qs['DOWN'][j][1])
        qs['UP'][i][0]-=q; qs['DOWN'][j][0]-=q
        if qs['UP'][i][0]<=1e-9:i+=1
        if qs['DOWN'][j][0]<=1e-9:j+=1
    return {'pairedShares':paired,'lockedEdgeUsdt':edge,'edgePerShare':edge/paired if paired else None}

f=ro(FLASH); b=ro(BASE); t=ro(TARGET)
try:
    fills=[dict(r) for r in f.execute('select fill_id,channel,purpose,side,price,shares,filled_at_ms,decision_id from our_fills where strategy_version=? and market_id=? order by filled_at_ms',(VERSION,MARKET))]
    dec=[dict(r) for r in f.execute('select decision_ms,seconds_left,phase,desired_portfolio_action,execution_choice,side,size,primary_reason,portfolio_state_json,economics_state_json,public_state_json from our_decisions where strategy_version=? and market_id=? order by decision_ms',(VERSION,MARKET))]
    basefills=[dict(r) for r in b.execute("select side,price,shares,filled_at_ms,channel,purpose from our_fills where market_id=? and strategy_version like 'UNIFIED_CONTROLLER_PAPER_V1%' order by filled_at_ms",(MARKET,))]
    res=t.execute('select winner,net_pnl_usdt from target_market_results where market_id=?',(MARKET,)).fetchone()
    def pack(fs):
        up=sum(float(x['shares']) for x in fs if x['side']=='UP'); dn=sum(float(x['shares']) for x in fs if x['side']=='DOWN'); cost=sum(float(x['shares'])*float(x['price']) for x in fs); return {'fills':len(fs),'upShares':up,'downShares':dn,'netShares':up-dn,'absNet':abs(up-dn),'cost':cost,'pair':fifo_pair(fs)}
    out={'marketId':MARKET,'winner':res['winner'] if res else None,'targetPnl':res['net_pnl_usdt'] if res else None,'stable':pack(fills),'base':pack(basefills),'stableFills':fills,'decisionCount':len(dec),'firstDecision':dec[0] if dec else None,'lastDecision':dec[-1] if dec else None}
    print(json.dumps(out,ensure_ascii=False,indent=2))
finally:
    f.close();b.close();t.close()
