from __future__ import annotations
import json
from pathlib import Path

TEST_ID="HFT_R2_GATEWAY_SESSION_EPOCH_FENCING_V1"
OUT=Path("data/research/hourly_novel_tests/hft_r2_gateway_session_epoch_fencing_v1_report.json")

class Sim:
    def __init__(self, obligation=12.0):
        self.obligation=float(obligation); self.confirmed=0.0; self.owner="OLD_PASSIVE"
        self.session_epoch=7; self.stale_control_mutation=0; self.dropped_authoritative_old_fill=0.0
        self.duplicate_owner=0; self.over_repair=0.0; self.lifecycle_violations=0; self.actions=[]
    @property
    def remainder(self): return max(0.0,self.obligation-self.confirmed)
    def reconnect(self): self.session_epoch+=1; self.actions.append(f"RECONNECT_EPOCH_{self.session_epoch}")
    def stale_control(self, kind, msg_epoch, target_owner):
        before=self.owner
        if msg_epoch < self.session_epoch:
            self.actions.append(f"FENCE_STALE_{kind}_E{msg_epoch}")
            return False
        self.owner=target_owner
        if self.owner!=before: self.stale_control_mutation+=1
        return True
    def authoritative_fill(self, qty, msg_epoch, stable_identity=True, execution_id=True):
        qty=float(qty)
        if stable_identity and execution_id:
            self.confirmed += qty
            if self.confirmed>self.obligation+1e-9:
                self.over_repair += self.confirmed-self.obligation; self.confirmed=self.obligation
            self.actions.append(f"ACCEPT_AUTHORITATIVE_FILL_{qty:g}_FROM_E{msg_epoch}")
            return True
        self.dropped_authoritative_old_fill += qty
        return False
    def authoritative_terminal(self): self.owner="CONTROLLER"; self.actions.append("AUTHORITATIVE_TERMINAL")
    def submit_new_passive(self, qty):
        if self.owner not in (None,"CONTROLLER"):
            self.duplicate_owner+=1; self.actions.append("SUPPRESS_DUPLICATE_REPLACEMENT"); return False
        qty=float(qty)
        if qty>self.remainder+1e-9:
            self.over_repair += qty-self.remainder; qty=self.remainder
        self.owner="NEW_PASSIVE"; self.actions.append(f"SUBMIT_NEW_PASSIVE_{qty:g}"); return True
    def complete_current(self, qty):
        if self.owner not in ("OLD_PASSIVE","NEW_PASSIVE","ACTIVE"):
            self.lifecycle_violations+=1; return
        self.confirmed += float(qty)
        if self.confirmed>self.obligation+1e-9:
            self.over_repair += self.confirmed-self.obligation; self.confirmed=self.obligation
        self.owner="CONTROLLER"; self.actions.append(f"COMPLETE_{qty:g}")

def scenario1():
    s=Sim(); s.reconnect()
    # Delayed old-session submit ACK must not resurrect/mutate ownership.
    s.authoritative_terminal(); s.stale_control("SUBMIT_ACK",7,"OLD_PASSIVE")
    exact=s.owner=="CONTROLLER" and s.remainder==12
    s.submit_new_passive(12); s.complete_current(12)
    return s, exact and s.remainder==0

def scenario2():
    s=Sim(); s.reconnect(); s.authoritative_terminal(); s.submit_new_passive(12)
    # Stale cancel ACK for old-session order must not cancel/release the fresh owner.
    s.stale_control("CANCEL_ACK",7,"CONTROLLER")
    exact=s.owner=="NEW_PASSIVE" and s.remainder==12
    s.complete_current(12)
    return s, exact and s.remainder==0

def scenario3():
    s=Sim(); s.reconnect()
    # A real fill can arrive on the old transport session; session fencing must not discard it.
    accepted=s.authoritative_fill(5,7,True,True)
    s.authoritative_terminal()
    exact=accepted and s.remainder==7
    s.submit_new_passive(7); s.complete_current(7)
    return s, exact and s.remainder==0

rows=[]
for name,fn in [("stale_submit_ack_cannot_resurrect_old_owner",scenario1),("stale_cancel_ack_cannot_release_fresh_owner",scenario2),("authoritative_old_session_fill_must_still_reconcile",scenario3)]:
    s,exact=fn()
    ok=exact and s.stale_control_mutation==0 and s.dropped_authoritative_old_fill==0 and s.duplicate_owner==0 and s.over_repair==0 and s.lifecycle_violations==0
    rows.append({"scenario":name,"passed":bool(ok),"terminalRemainder":s.remainder,"staleControlMutationCount":s.stale_control_mutation,"genuineOldSessionFillDroppedQty":s.dropped_authoritative_old_fill,"duplicateOwnerCount":s.duplicate_owner,"overRepairQty":s.over_repair,"lifecycleViolationCount":s.lifecycle_violations,"actions":s.actions})
summary={"testId":TEST_ID,"testedAt":"2026-08-25T02:57:00+08:00","axis":"R2_AUTONOMOUS_REPAIR_GATEWAY_SESSION_EPOCH_FENCING","scenarioCount":len(rows),"passed":sum(r["passed"] for r in rows),"staleControlMutationCount":sum(r["staleControlMutationCount"] for r in rows),"genuineOldSessionFillDroppedQty":sum(r["genuineOldSessionFillDroppedQty"] for r in rows),"duplicateOwnerCount":sum(r["duplicateOwnerCount"] for r in rows),"overRepairQty":sum(r["overRepairQty"] for r in rows),"lifecycleViolationCount":sum(r["lifecycleViolationCount"] for r in rows),"status":"TESTED_KEEP_SIGNAL" if all(r["passed"] for r in rows) else "TESTED_REJECTED","evidenceClass":"STRUCTURAL_LIFECYCLE_ONLY","pnlClaim":False,"scenarios":rows}
OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False))
