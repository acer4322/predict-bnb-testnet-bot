from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_adaptive_option_smoke_v1 import run_adaptive_smoke

OUT=ROOT/"data"/"research"/"execution_aware_fill_lifecycle_v0"
MARKETS=[1572594,1572805,1573000]
POLICIES=[("offset2","wait"),("offset2","offset0"),("offset2","offset1"),("offset2","offset2"),("offset1","wait"),("offset1","offset0"),("offset1","offset2")]
rows=[]
for maintain,repair in POLICIES:
    for market in MARKETS:
        r=run_adaptive_smoke(market,maintain,repair)
        rows.append(r)
        print(json.dumps(r,ensure_ascii=False),flush=True)
summary=[]
for maintain,repair in POLICIES:
    rr=[r for r in rows if r["maintainMode"]==maintain and r["repairMode"]==repair]
    summary.append({"maintainMode":maintain,"repairMode":repair,"totalPnl":sum(r["realizedPnl"] for r in rr),"positiveMarkets":sum(r["realizedPnl"]>0 for r in rr),"negativeMarkets":sum(r["realizedPnl"]<0 for r in rr),"totalTrackingError":sum(r["finalAbsTrackingError"] for r in rr),"totalMakerShares":sum(r["makerFilledShares"] for r in rr),"totalTakerShares":sum(r["takerFilledShares"] for r in rr)})
summary=sorted(summary,key=lambda x:x["totalPnl"],reverse=True)
report={"version":"HFT_R2_ADAPTIVE_OPTION_PILOT_V1_REPORT","researchOnly":True,"markets":MARKETS,"rows":rows,"summary":summary,"best":summary[0]}
(OUT/"hft_r2_adaptive_option_pilot_v1_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print("SUMMARY")
print(json.dumps(summary,ensure_ascii=False,indent=2))
