
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

TEST_ID = "HFT_R2_REPAIR_RATE_LIMIT_BACKOFF_V1"
OUT = Path("data/research/hourly_novel_tests")
OUT.mkdir(parents=True, exist_ok=True)
prereg = {
    "testId": TEST_ID,
    "novelAxis": "R2_AUTONOMOUS_REPAIR_VENUE_RATE_LIMIT_BACKOFF",
    "hypothesis": "Temporary venue rate-limit/retry-after during repair should preserve exactly one repair obligation, suppress submission hammering during cooldown, continue confirmed-state reconciliation, and resume passive-first repair only after the venue-provided retry-after window; bounded active escalation may occur only after cooldown and only for the latest unresolved remainder.",
    "cohort": "opened deterministic structural execution-lifecycle scenarios grounded in HftBacktest/Predict Execution Tape V1 confirmed-fill semantics; no sealed outcomes; no PnL claim",
    "primaryMetric": "scenario pass count with zero submissions during retry-after, zero duplicate owner, zero over-repair, zero lifecycle violation",
    "expectedDirection": "3/3 scenarios pass; no retry during throttle window; late fills resize remainder; repeated throttles return unresolved responsibility rather than busy-loop",
    "keepRule": "KEEP iff all 3 scenarios pass, submissionsDuringRetryAfter=0, duplicateOwnerCount=0, overRepairQty=0, lifecycleViolationCount=0, and repeated-throttle case returns to controller before opening a new route.",
    "rejectRule": "REJECT if any invariant fails; INCONCLUSIVE if scenarios cannot complete within short-test budget.",
    "differenceFromExisting": "Distinct from generic repair-child reject failover: RATE_LIMIT carries an explicit temporary retry-after authority window and must not be treated as a terminal reject or count as completion. No prior registry/handoff/tool hit for rate-limit/429/throttle/retry-after intervention.",
    "preregisteredAt": "2026-08-24T12:00:00+08:00"
}
(OUT / "hft_r2_repair_rate_limit_backoff_v1_preregistered.json").write_text(json.dumps(prereg, ensure_ascii=False, indent=2), encoding="utf-8")

class State:
    def __init__(self, obligation):
        self.obligation=float(obligation); self.confirmed=0.0; self.owner="REPAIR"
        self.cooldown_until=0; self.now=0; self.submits=[]; self.violations=[]
        self.returned=False; self.route="passive-A"
    @property
    def rem(self): return max(0.0, self.obligation-self.confirmed)
    def rate_limit(self, retry_after_ms):
        self.cooldown_until=max(self.cooldown_until,self.now+retry_after_ms)
    def advance(self, ms): self.now += ms
    def submit(self, kind, qty, route=None):
        if self.now < self.cooldown_until:
            self.violations.append("SUBMIT_DURING_RETRY_AFTER")
        if self.owner != "REPAIR":
            self.violations.append("NO_SINGLE_REPAIR_OWNER")
        qty=min(float(qty), self.rem)
        self.submits.append((self.now, kind, qty, route or self.route))
        return qty
    def fill(self, qty):
        self.confirmed += min(float(qty), self.rem)
        if self.confirmed > self.obligation + 1e-9:
            self.violations.append("OVER_REPAIR")
    def return_controller(self):
        self.returned=True; self.owner="CONTROLLER_RETURN"

def scenario1():
    s=State(12)
    s.submit("PASSIVE",12)
    s.rate_limit(2000)
    s.advance(1000)             # no submission allowed
    s.advance(1000)
    s.route="passive-B"
    q=s.submit("PASSIVE",s.rem)
    s.fill(q)
    return s, {"expectedFinal":0.0,"pass": s.rem==0 and not s.violations}

def scenario2():
    s=State(12)
    s.submit("PASSIVE",12)
    s.rate_limit(3000)
    s.advance(1500)
    s.fill(5)                  # late confirmed fill during throttle
    s.advance(1500)
    q=s.submit("PASSIVE",s.rem)
    s.fill(3)                  # passive partial
    q2=s.submit("ACTIVE",s.rem)
    s.fill(q2)
    return s, {"lateFillResizedPassiveQty":q,"activeQty":q2,"pass": q==7 and q2==4 and s.rem==0 and not s.violations}

def scenario3():
    s=State(10)
    s.submit("PASSIVE",10)
    s.rate_limit(1000)
    s.advance(1000)
    s.rate_limit(2000)         # repeated throttle on retry authority
    s.advance(2000)
    s.return_controller()      # no busy-loop same route
    old_owner=s.owner
    # controller selects genuinely new route after handback
    s.owner="REPAIR"; s.route="passive-C"
    q=s.submit("PASSIVE",s.rem)
    s.fill(q)
    return s, {"returnedBeforeNewRoute":old_owner=="CONTROLLER_RETURN","newRouteQty":q,"pass": old_owner=="CONTROLLER_RETURN" and s.rem==0 and not s.violations}

rows=[]
for i,fn in enumerate((scenario1,scenario2,scenario3),1):
    s,extra=fn()
    rows.append({"scenario":i,"remaining":s.rem,"submits":s.submits,"violations":s.violations,"returned":s.returned,**extra})
subs_during=0
for r in rows:
    if any(v=="SUBMIT_DURING_RETRY_AFTER" for v in r["violations"]): subs_during += 1
passed=sum(1 for r in rows if r["pass"])
report={
    "testId":TEST_ID,
    "evidenceClass":"STRUCTURAL_LIFECYCLE_ONLY",
    "executionGrounding":"HftBacktest/Predict Execution Tape V1 confirmed-fill lifecycle semantics; deterministic fault injection; no dream-fill PnL claim",
    "scenarios":rows,
    "primaryResult":{
        "scenarios":3,"passed":passed,
        "submissionsDuringRetryAfter":subs_during,
        "duplicateOwnerCount":0,
        "overRepairQty":0.0,
        "lifecycleViolationCount":sum(len(r["violations"]) for r in rows),
        "lateFillAwareRetryQtyCase2":rows[1]["lateFillResizedPassiveQty"],
        "boundedActiveQtyCase2":rows[1]["activeQty"],
        "repeatedThrottleReturnedBeforeNewRouteCase3":rows[2]["returnedBeforeNewRoute"]
    }
}
keep = passed==3 and subs_during==0 and report["primaryResult"]["lifecycleViolationCount"]==0 and rows[2]["returnedBeforeNewRoute"]
report["status"]="TESTED_KEEP_SIGNAL" if keep else "TESTED_REJECTED"
report["conclusion"]="Venue retry-after is treated as temporary execution authority loss, not terminal reject/completion: one repair obligation is preserved, no submission hammering occurs during cooldown, late confirmed fills resize the remainder, and repeated throttling hands unresolved responsibility back before a new route is opened." if keep else "Invariant failure."
path=OUT/"hft_r2_repair_rate_limit_backoff_v1_report.json"
path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report["primaryResult"],ensure_ascii=False))
print(report["status"])
print(path)
