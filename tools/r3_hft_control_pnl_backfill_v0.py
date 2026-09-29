from __future__ import annotations
import json, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as r3c

DB=ROOT/'data/r3_dual_paper_shadow_v2.db'
TDB=ROOT/'data/target_wallet_official_v1.db'
MIDS=[1649541,1649545,1649562,1649744,1649747]

def winner(mid):
    c=sqlite3.connect(TDB)
    for q in [
        "select winner from markets where market_id=?",
        "select outcome from market_settlements where market_id=?",
        "select winner_side from settlements where market_id=?",
    ]:
        try:
            r=c.execute(q,(mid,)).fetchone()
            if r and r[0] in ('UP','DOWN'):
                c.close(); return r[0]
        except Exception: pass
    c.close(); return None

def summarize(rep,w):
    s=rep['studentRollout']; p=s['finalPortfolio']
    up=float(p.get('combined_up') or p.get('up_shares') or 0.0)
    down=float(p.get('combined_down') or p.get('down_shares') or 0.0)
    # Inventory feature names may differ; reconstruct from detailed inventory if present.
    if up==0 and down==0:
        up=float((p.get('maker_up') or 0)+(p.get('taker_up') or 0))
        down=float((p.get('maker_down') or 0)+(p.get('taker_down') or 0))
    cost=float(s.get('makerCostUsdt') or 0)+float(s.get('takerCostUsdt') or 0)+float(s.get('takerFeesUsdt') or 0)
    payout=up if w=='UP' else down if w=='DOWN' else None
    pnl=(payout-cost) if payout is not None else None
    return {'winner':w,'upShares':up,'downShares':down,'costUsdt':cost,'payoutUsdt':payout,'pnlUsdt':pnl,'makerFillEvents':s['makerFillEvents'],'makerFilledShares':s['makerFilledShares'],'takerFills':s['takerFills'],'finalAbsNet':p.get('combined_abs_net'),'worstCaseFloor':p.get('worst_case_floor'),'makerNet':p.get('maker_net')}

def main():
    out=[]
    for mid in MIDS:
        w=winner(mid)
        A=r3c.run_market(mid,False); B=r3c.run_market(mid,True)
        out.append({'marketId':mid,'baseline':summarize(A,w),'contextV4':summarize(B,w)})
    path=ROOT/'data/research/r3_v0/r3_hft_control_forward5_pnl_backfill_v0.json'
    path.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,indent=2))
if __name__=='__main__': main()
