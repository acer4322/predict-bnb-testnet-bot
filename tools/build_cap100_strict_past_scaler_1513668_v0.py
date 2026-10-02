from __future__ import annotations
import json,sqlite3,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'strategy_target_compare_v1.db'
VER='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'
MID=1513668
MAKER_BUDGET=80.0
BASE_SHARES=18.0
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
hist=con.execute("select market_id,sum(price*shares) maker_notional,count(*) fills from our_fills where strategy_version=? and channel='MAKER' and market_id<? group by market_id order by market_id",(VER,MID)).fetchall()
vals=[float(r['maker_notional']) for r in hist if r['maker_notional'] is not None and float(r['maker_notional'])>0]
median=statistics.median(vals); scale=min(1.0,MAKER_BUDGET/median); child=BASE_SHARES*scale
rows=con.execute("select side,price,shares,filled_at_ms from our_fills where strategy_version=? and market_id=? and channel='MAKER' order by filled_at_ms",(VER,MID)).fetchall()
up=down=cost=0.0
for r in rows:
    q=child
    p=float(r['price']); cost+=p*q
    if str(r['side'])=='UP':up+=q
    else:down+=q
winner='UP'; pnl=(up if winner=='UP' else down)-cost
out={'version':'CAP100_STRICT_PAST_TRAJECTORY_SCALER_1513668_V0','marketId':MID,'strictPastCalibration':{'marketsBeforeTest':len(vals),'medianMakerNotionalUsdt':median,'makerBudgetUsdt':MAKER_BUDGET,'baseChildShares':BASE_SHARES,'scale':scale,'childShares':child},'frozenTrajectoryAudit':{'sourceMakerFills':len(rows),'scaledMakerCostUsdt':cost,'scaledUpShares':up,'scaledDownShares':down,'winner':winner,'diagnosticPnlUsdt':pnl,'remainingTotalCapAfterMakerUsdt':100.0-cost},'guards':['Calibration uses only R2 markets with market_id lower than the test market.','No future fills or test-market turnover are used to choose childShares.','Frozen-trajectory audit preserves original R2 action chronology; a true scaled controller can choose different later actions.']}
p=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'cap100_strict_past_trajectory_scaler_1513668_v0.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));con.close()
