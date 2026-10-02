from __future__ import annotations
import sqlite3, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MID=1513668
DB=sqlite3.connect(ROOT/'data'/'strategy_target_compare_v1.db'); DB.row_factory=sqlite3.Row
R2='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'
CAP='UNIFIED_PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_FORWARD_PAPER'

def rows(ver):
    return DB.execute('select * from our_fills where market_id=? and strategy_version=? order by filled_at_ms',(MID,ver)).fetchall()

def summarize(ver):
    rs=rows(ver); maker=[r for r in rs if str(r['channel'])=='MAKER']; taker=[r for r in rs if str(r['channel'])=='TAKER']
    def notional(xs): return sum(float(r['price'])*float(r['shares']) for r in xs)
    return {'fills':len(rs),'makerFills':len(maker),'takerFills':len(taker),'makerNotional':notional(maker),'takerNotional':notional(taker),'makerShares':sum(float(r['shares']) for r in maker),'takerShares':sum(float(r['shares']) for r in taker)}

r2=rows(R2); maker=[r for r in r2 if str(r['channel'])=='MAKER']; taker=[r for r in r2 if str(r['channel'])=='TAKER']
maker_notional=sum(float(r['price'])*float(r['shares']) for r in maker)
scale=80.0/maker_notional if maker_notional else 0.0
scaled_child=18.0*scale
# Preserve original sequence; compute scaled realized cashflow using known winner UP.
up=down=cost=0.0
for r in r2:
    q=float(r['shares'])*scale; px=float(r['price']); cost += q*px
    if str(r['side'])=='UP': up+=q
    else: down+=q
# diagnostic: if all R2 taker also scales same, total may exceed 100; report both maker-only and all-channel scale options.
all_notional=sum(float(r['price'])*float(r['shares']) for r in r2)
scale_all=100.0/all_notional if all_notional else 0.0
up_all=down_all=cost_all=0.0
for r in r2:
    q=float(r['shares'])*scale_all; px=float(r['price']); cost_all += q*px
    if str(r['side'])=='UP': up_all+=q
    else: down_all+=q
out={
 'version':'CAP100_TRAJECTORY_PRESERVING_SCALE_1513668_V0','marketId':MID,'winner':'UP',
 'sourceR2':summarize(R2),'hardCapPaper':summarize(CAP),
 'maker80Scale':{'scale':scale,'childSharesEquivalent':scaled_child,'scaledAllChannelCostUsdt':cost,'upShares':up,'downShares':down,'diagnosticPnlUsdt':up-cost,'note':'maker path normalized to $80; original Taker sequence also scaled only to inspect lifecycle shape, not a deployable allocation'},
 'total100Scale':{'scale':scale_all,'childSharesEquivalent':18.0*scale_all,'totalCostUsdt':cost_all,'upShares':up_all,'downShares':down_all,'diagnosticPnlUsdt':up_all-cost_all,'note':'all original R2 fills scaled uniformly so total realized buy notional is $100; preserves action chronology and relative sizing exactly'},
 'interpretationGuard':'This is a frozen-trajectory capital-shape audit, not a closed-loop replay. Scaling changes inventory features, so a true controller may choose different later actions.'
}
print(json.dumps(out,ensure_ascii=False,indent=2))
DB.close()
