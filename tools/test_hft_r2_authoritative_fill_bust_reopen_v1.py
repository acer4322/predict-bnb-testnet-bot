from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/hourly_novel_tests/hft_r2_authoritative_fill_bust_reopen_v1_report.json'

class Repair:
    def __init__(self, obligation: float):
        self.required=float(obligation)
        self.confirmed=0.0
        self.owner=None
        self.duplicate_owner_count=0
        self.over_repair=0.0
        self.lifecycle_violations=0
        self.active_replay_after_bust=0
        self.events=[]
    @property
    def unresolved(self): return max(0.0,self.required-self.confirmed)
    def open_owner(self, channel):
        if self.owner is not None:
            self.duplicate_owner_count+=1
            return
        self.owner=channel; self.events.append(['OPEN',channel,self.unresolved])
    def terminal(self):
        self.events.append(['TERMINAL',self.owner,self.unresolved]); self.owner=None
    def fill(self, qty):
        q=float(qty)
        if q<0: self.lifecycle_violations+=1; return
        self.confirmed+=q
        if self.confirmed>self.required+1e-9:
            self.over_repair+=self.confirmed-self.required
        self.events.append(['FILL',q,self.confirmed,self.unresolved])
    def authoritative_bust(self, qty):
        q=float(qty)
        # Authoritative correction may legitimately move the confirmed frontier backward.
        if q<=0 or q>self.confirmed+1e-9:
            self.lifecycle_violations+=1; return
        self.confirmed-=q
        self.events.append(['AUTHORITATIVE_BUST',q,self.confirmed,self.unresolved])
        # Any prior terminal-complete economic obligation is reopened by exact corrected qty.
        if self.owner is not None:
            self.lifecycle_violations+=1
            self.owner=None
        self.open_owner('PASSIVE')
    def passive_fill_and_close(self, qty):
        if self.owner!='PASSIVE': self.lifecycle_violations+=1
        self.fill(qty)
        if self.unresolved<=1e-9: self.terminal()


def scenario1():
    r=Repair(12); r.open_owner('PASSIVE'); r.fill(7); r.terminal()
    r.authoritative_bust(3) # confirmed 4, exact unresolved 8
    reopened=r.unresolved
    r.passive_fill_and_close(8)
    return r,reopened,8.0

def scenario2():
    r=Repair(12); r.open_owner('PASSIVE'); r.fill(12); r.terminal()
    was_complete=(r.unresolved==0 and r.owner is None)
    r.authoritative_bust(5) # terminal-complete obligation must reopen to 5
    reopened=r.unresolved
    owner_after=r.owner
    r.passive_fill_and_close(5)
    r.events.append(['WAS_COMPLETE_BEFORE_BUST',was_complete,'OWNER_AFTER_BUST',owner_after])
    return r,reopened,5.0

def scenario3():
    r=Repair(12); r.open_owner('PASSIVE'); r.fill(8); r.terminal()
    r.open_owner('ACTIVE'); r.fill(4); r.terminal() # completed through bounded active
    r.authoritative_bust(2) # correction to active fill; must reopen passive-first, not replay active
    reopened=r.unresolved
    if r.owner=='ACTIVE': r.active_replay_after_bust+=1
    owner_after=r.owner
    r.passive_fill_and_close(2)
    r.events.append(['OWNER_AFTER_BUST',owner_after])
    return r,reopened,2.0

rows=[]
for i,fn in enumerate((scenario1,scenario2,scenario3),1):
    r,reopened,expected=fn()
    passed=(abs(reopened-expected)<1e-9 and r.duplicate_owner_count==0 and r.over_repair==0 and r.lifecycle_violations==0 and r.active_replay_after_bust==0 and r.unresolved==0 and r.owner is None)
    rows.append({'scenario':i,'passed':passed,'reopenedQty':reopened,'expectedReopenedQty':expected,'duplicateOwnerCount':r.duplicate_owner_count,'overRepairQty':r.over_repair,'lifecycleViolationCount':r.lifecycle_violations,'activeReplayAfterBust':r.active_replay_after_bust,'terminalUnresolvedQty':r.unresolved,'events':r.events})
passed=sum(int(x['passed']) for x in rows)
report={
 'testId':'HFT_R2_AUTHORITATIVE_FILL_BUST_REOPEN_V1',
 'researchOnly':True,
 'evidenceClass':'STRUCTURAL_LIFECYCLE_ONLY_NOT_PNL',
 'scenarios':3,
 'passed':passed,
 'primaryResult':{
   'exactReopenCases':sum(abs(x['reopenedQty']-x['expectedReopenedQty'])<1e-9 for x in rows),
   'terminalCompleteReopened':rows[1]['passed'],
   'duplicateOwnerCount':sum(x['duplicateOwnerCount'] for x in rows),
   'overRepairQty':sum(x['overRepairQty'] for x in rows),
   'lifecycleViolationCount':sum(x['lifecycleViolationCount'] for x in rows),
   'activeReplayAfterBustCount':sum(x['activeReplayAfterBust'] for x in rows),
   'terminalUnresolvedQtyMax':max(x['terminalUnresolvedQty'] for x in rows)
 },
 'status':'TESTED_KEEP_SIGNAL' if passed==3 else 'TESTED_REJECTED',
 'conclusion':'Authoritative execution corrections are distinct from stale/out-of-order evidence: an explicit fill bust may legitimately move confirmed own-state backward. Exact corrected quantity is restored to the economic repair obligation, even after prior completion, and recovery re-enters passive-first with singular ownership rather than replaying stale active execution.',
 'rows':rows
}
OUT.parent.mkdir(parents=True,exist_ok=True)
OUT.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'report':str(OUT),'status':report['status'],'passed':passed,'primaryResult':report['primaryResult']}))
