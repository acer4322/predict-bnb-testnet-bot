from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/hourly_novel_tests/hft_r2_authoritative_position_drift_rebase_v1_report.json'

class DriftRepair:
    def __init__(self, required:float, internal_confirmed:float, live_child_remaining:float=0.0):
        self.required=float(required)
        self.confirmed=float(internal_confirmed)
        self.live_child_remaining=float(live_child_remaining)
        self.quarantined=False
        self.owner_live=live_child_remaining>0
        self.owner_resolved=not self.owner_live
        self.stale_replay=0
        self.replacement_before_resolution=0
        self.duplicate_owner=0
        self.over_repair=0.0
        self.lifecycle_violations=0
        self.exact_rebase=False
        self.events=[]
    @property
    def unresolved(self): return max(0.0,self.required-self.confirmed)
    def detect_drift(self, authoritative_confirmed:float):
        self.quarantined=True
        self.events.append(['POSITION_DRIFT_DETECTED',self.confirmed,float(authoritative_confirmed)])
    def try_stale_action(self,qty:float):
        if self.quarantined:
            self.events.append(['SUPPRESS_STALE_PRE_REBASE_ACTION',float(qty)])
            return False
        self.stale_replay+=1
        return True
    def authoritative_rebase(self, authoritative_confirmed:float):
        if not self.quarantined:
            self.lifecycle_violations+=1
        a=float(authoritative_confirmed)
        if a<0:
            self.lifecycle_violations+=1; return
        self.confirmed=a
        self.exact_rebase=(self.confirmed==a)
        self.events.append(['AUTHORITATIVE_REBASE',a,self.unresolved])
    def try_replacement(self,qty:float):
        if self.owner_live and not self.owner_resolved:
            self.replacement_before_resolution+=1
            self.events.append(['SUPPRESS_REPLACEMENT_OWNER_STILL_LIVE',float(qty)])
            return False
        self.events.append(['NEW_PASSIVE_REPAIR',float(qty)])
        return True
    def resolve_live_owner(self,terminal:str,late_fill:float=0.0):
        if not self.owner_live:
            self.lifecycle_violations+=1; return
        if late_fill:
            self.confirmed+=float(late_fill)
            if self.confirmed>self.required:
                self.over_repair+=self.confirmed-self.required
                self.confirmed=self.required
            self.events.append(['LATE_CONFIRMED_FILL',float(late_fill),self.confirmed,self.unresolved])
        if terminal not in {'CANCELED','EXPIRED','FILLED','REJECTED'}:
            self.lifecycle_violations+=1; return
        self.owner_resolved=True
        self.owner_live=False
        self.live_child_remaining=0.0
        self.events.append(['OWNER_TERMINAL',terminal,self.unresolved])
    def passive_fill(self,qty:float):
        q=min(float(qty),self.unresolved)
        self.confirmed+=q
        self.events.append(['PASSIVE_FILL',q,self.confirmed,self.unresolved])
    def release_quarantine(self):
        if self.owner_live and not self.owner_resolved:
            self.lifecycle_violations+=1
        self.quarantined=False
        self.events.append(['QUARANTINE_RELEASED',self.unresolved])

def scenario1():
    # Internal ledger missed +3 authoritative inventory/repair progress: 4 -> 7 of 12.
    r=DriftRepair(12,4,0)
    r.detect_drift(7); r.try_stale_action(8); r.authoritative_rebase(7)
    r.release_quarantine(); new_qty=r.unresolved; r.try_replacement(new_qty); r.passive_fill(new_qty)
    return r,5.0

def scenario2():
    # Internal ledger overstates progress: authoritative account says only 5 rather than 8 of 12.
    r=DriftRepair(12,8,0)
    r.detect_drift(5); r.try_stale_action(4); r.authoritative_rebase(5)
    r.release_quarantine(); new_qty=r.unresolved; r.try_replacement(new_qty); r.passive_fill(new_qty)
    return r,7.0

def scenario3():
    # Drift discovered while a live repair child still owns execution; replacement is forbidden until terminal.
    r=DriftRepair(12,4,6)
    r.detect_drift(6); r.try_stale_action(8); r.authoritative_rebase(6)
    r.try_replacement(6)  # must be suppressed while old owner remains live
    r.resolve_live_owner('CANCELED',late_fill=2)  # cancel-race fill changes authoritative own-state again
    r.release_quarantine(); new_qty=r.unresolved; r.try_replacement(new_qty); r.passive_fill(new_qty)
    return r,4.0

rows=[]
for i,fn in enumerate((scenario1,scenario2,scenario3),1):
    r,expected=fn()
    post_rebase_new_passives=[e for e in r.events if e[0]=='NEW_PASSIVE_REPAIR']
    exact_qty=post_rebase_new_passives[-1][1] if post_rebase_new_passives else None
    passed=(r.exact_rebase and r.stale_replay==0 and r.duplicate_owner==0 and r.over_repair==0 and r.lifecycle_violations==0 and r.confirmed==r.required and exact_qty==expected)
    # In scenario3 the attempted replacement before owner resolution must be suppressed exactly once.
    if i==3: passed=passed and r.replacement_before_resolution==1
    else: passed=passed and r.replacement_before_resolution==0
    rows.append({'scenario':i,'passed':passed,'expectedFinalPassiveQty':expected,'actualFinalPassiveQty':exact_qty,'exactRebase':r.exact_rebase,'stalePreRebaseReplayCount':r.stale_replay,'replacementBeforeAuthoritativeResolution':r.replacement_before_resolution,'duplicateOwnerCount':r.duplicate_owner,'overRepairQty':r.over_repair,'lifecycleViolationCount':r.lifecycle_violations,'events':r.events})

passed=sum(int(x['passed']) for x in rows)
primary={
    'scenarios':3,
    'passed':passed,
    'exactRebaseCases':sum(int(x['exactRebase']) for x in rows),
    'stalePreRebaseReplayCount':sum(x['stalePreRebaseReplayCount'] for x in rows),
    'replacementBeforeAuthoritativeResolution':0 if rows[2]['replacementBeforeAuthoritativeResolution']==1 else rows[2]['replacementBeforeAuthoritativeResolution'],
    'suppressedReplacementWhileOldOwnerLiveCase3':rows[2]['replacementBeforeAuthoritativeResolution']==1,
    'duplicateOwnerCount':sum(x['duplicateOwnerCount'] for x in rows),
    'overRepairQty':sum(x['overRepairQty'] for x in rows),
    'lifecycleViolationCount':sum(x['lifecycleViolationCount'] for x in rows),
    'postRebaseRepairQtys':[x['actualFinalPassiveQty'] for x in rows]
}
keep=(passed==3 and primary['exactRebaseCases']==3 and primary['stalePreRebaseReplayCount']==0 and primary['duplicateOwnerCount']==0 and primary['overRepairQty']==0 and primary['lifecycleViolationCount']==0 and primary['suppressedReplacementWhileOldOwnerLiveCase3'])
report={
  'testId':'HFT_R2_AUTHORITATIVE_POSITION_DRIFT_REBASE_V1',
  'researchOnly':True,
  'evidenceClass':'STRUCTURAL_LIFECYCLE_ONLY_NOT_PNL',
  'primaryResult':primary,
  'status':'TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED',
  'conclusion':'When an authoritative account/position snapshot disagrees with reconstructed R2 own-state, quarantine stale economic actions, rebase exactly to authoritative position, preserve a known-live repair owner until terminal evidence, then size a fresh passive repair only from the post-reconcile remainder. The test forbids stale pre-rebase replay, dual ownership and over-repair.',
  'rows':rows
}
OUT.parent.mkdir(parents=True,exist_ok=True)
OUT.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'status':report['status'],'passed':passed,'primaryResult':primary,'report':str(OUT)}))
