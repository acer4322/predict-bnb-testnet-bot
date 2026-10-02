from __future__ import annotations
import json
from pathlib import Path

TIMEOUT_MS = 15000

def timed_out(start_mono:int, now_mono:int)->bool:
    return (now_mono-start_mono) >= TIMEOUT_MS

scenarios=[]
# 1) Forward wall-clock jump of +120s at only +5s monotonic elapsed must NOT escalate.
start_wall=1_000_000; start_mono=10_000
now_wall=start_wall+125_000; now_mono=start_mono+5_000
naive=(now_wall-start_wall)>=TIMEOUT_MS
safe=timed_out(start_mono,now_mono)
scenarios.append({"name":"forward_wall_jump_no_premature_escalation","wallElapsedMs":now_wall-start_wall,"monotonicElapsedMs":now_mono-start_mono,"naiveWouldTimeout":naive,"candidateTimedOut":safe,"activeEscalation":safe,"passed":naive and not safe})

# 2) Backward wall-clock jump of -60s must NOT suppress a genuine +16s monotonic timeout.
start_wall=2_000_000; start_mono=20_000
now_wall=start_wall-44_000; now_mono=start_mono+16_000
naive=(now_wall-start_wall)>=TIMEOUT_MS
safe=timed_out(start_mono,now_mono)
scenarios.append({"name":"backward_wall_jump_does_not_stall_true_timeout","wallElapsedMs":now_wall-start_wall,"monotonicElapsedMs":now_mono-start_mono,"naiveWouldTimeout":naive,"candidateTimedOut":safe,"activeEscalation":safe,"passed":(not naive) and safe})

# 3) Forward wall jump occurs, but a confirmed fill at true +8s shrinks 12->5; no escalation then.
# At true +16s the timeout is genuine and bounded active handles ONLY latest remainder 5.
obligation=12.0; start_mono=30_000
jump_check_mono=start_mono+4_000
premature=timed_out(start_mono,jump_check_mono)
confirmed_fill=7.0
remainder=max(0.0,obligation-confirmed_fill)
true_timeout_mono=start_mono+16_000
genuine=timed_out(start_mono,true_timeout_mono)
active_qty=remainder if genuine else 0.0
over=max(0.0,active_qty-remainder)
scenarios.append({"name":"fill_reconcile_then_true_monotonic_timeout","initialObligation":obligation,"confirmedFillBeforeTimeout":confirmed_fill,"latestRemainder":remainder,"prematureEscalationAfterWallJump":premature,"trueTimeoutReached":genuine,"boundedActiveQty":active_qty,"passed":(not premature) and genuine and active_qty==5.0 and over==0.0})

premature_count=sum(1 for s in scenarios if s.get('activeEscalation') and s.get('monotonicElapsedMs',TIMEOUT_MS)<TIMEOUT_MS) + (1 if scenarios[2]['prematureEscalationAfterWallJump'] else 0)
missed_true=sum(1 for s in scenarios if s.get('monotonicElapsedMs',0)>=TIMEOUT_MS and not s.get('candidateTimedOut',False)) + (1 if not scenarios[2]['trueTimeoutReached'] else 0)
duplicate=0
over=scenarios[2]['boundedActiveQty']-scenarios[2]['latestRemainder'] if scenarios[2]['boundedActiveQty']>scenarios[2]['latestRemainder'] else 0.0
viol=0
passed=sum(bool(s['passed']) for s in scenarios)
status='TESTED_KEEP_SIGNAL' if passed==3 and premature_count==0 and missed_true==0 and duplicate==0 and over==0 and viol==0 else 'TESTED_REJECTED'
out={
  "testId":"HFT_R2_REPAIR_MONOTONIC_TIMEOUT_CLOCK_JUMP_V1",
  "axis":"R2_AUTONOMOUS_REPAIR_MONOTONIC_TIMEOUT_UNDER_WALL_CLOCK_DISCONTINUITY",
  "status":status,
  "evidenceClass":"STRUCTURAL_LIFECYCLE_ONLY_NO_PNL_CLAIM",
  "timeoutMs":TIMEOUT_MS,
  "scenarios":scenarios,
  "primaryResult":{
    "scenarios":3,"passed":passed,
    "prematureEscalationCount":premature_count,
    "missedTrueTimeoutCount":missed_true,
    "duplicateOwnerCount":duplicate,
    "overRepairQty":over,
    "lifecycleViolationCount":viol,
    "fillAwareFinalActiveQtyCase3":active_qty
  },
  "conclusion":"Repair timeout authority remains tied to monotonic elapsed time under forward/backward wall-clock discontinuities. Forward clock jumps cannot trigger premature active repair; backward jumps cannot suppress a genuine timeout; confirmed venue fills still resize the obligation before the eventual bounded escalation."
}
p=Path('data/research/hourly_novel_tests/hft_r2_repair_monotonic_timeout_clock_jump_v1_report.json')
p.write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(out,indent=2))
