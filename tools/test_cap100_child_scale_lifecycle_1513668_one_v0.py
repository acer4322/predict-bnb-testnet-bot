from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_cap100_closed_loop_v0 as cl
from src.predict_bot import unified_controller_cap100_shadow_v1 as mod
p=argparse.ArgumentParser();p.add_argument('--shares',type=float,required=True);a=p.parse_args(); old=mod.SHARES;mod.SHARES=a.shares
try:r=cl.run_market(1513668,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_mode='hft',diagnostic_only=False)
finally:mod.SHARES=old
c=r['closedLoop'];led=c['ledger'];rm=c['runMetrics'];dec=r['decisionRows'];mts=[int(x['decisionMs']) for x in dec if str(x.get('executionChoice'))=='MAKER'];tts=[int(x['decisionMs']) for x in dec if str(x.get('executionChoice'))=='TAKER'];first=int(dec[0]['decisionMs']) if dec else None
print(json.dumps({'childShares':a.shares,'makerPlacements':c['makerPlacements'],'makerFillEvents':c['makerFillEvents'],'makerFilledShares':c['makerFilledShares'],'makerCostUsdt':led['makerCostUsdt'],'takerFills':c['takerFills'],'takerCostUsdt':led['takerCostUsdt'],'makerCapBlocks':rm['makerCapBlocks'],'takerCapBlocks':rm['takerCapBlocks'],'residualWakes':rm['residualWakes'],'unresolvedGuardEntries':rm['unresolvedGuardEntries'],'finalAbsNet':c['finalPortfolio']['combined_abs_net'],'realizedPnlUsdt':led['realizedPnlUsdt'],'lastMakerDecisionOffsetMs':(max(mts)-first) if mts and first is not None else None,'firstTakerDecisionOffsetMs':(min(tts)-first) if tts and first is not None else None},ensure_ascii=False))
