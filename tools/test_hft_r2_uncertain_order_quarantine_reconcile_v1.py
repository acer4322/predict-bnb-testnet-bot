from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/hourly_novel_tests'

class Recovery:
    def __init__(self):
        self.uncertain=False; self.frozen=False; self.net=0.0
        self.passive=False; self.active=False; self.reconciled=False
        self.owner_preserved=False; self.violations=[]; self.actions=[]
        self.outstanding_passive=False
    def event(self, typ, side=None, qty=0.0):
        if typ in {'ORDER_STATE_UNKNOWN','CANCEL_ACK_TIMEOUT'}:
            self.uncertain=True; self.frozen=True; self.owner_preserved=True
            self.actions.append('QUARANTINE_PRESERVE_OWNER')
            return
        if typ=='ECONOMIC_RETRY_REQUEST':
            if self.uncertain or self.frozen:
                self.actions.append('SUPPRESS_DUPLICATE_RETRY')
            else:
                self.violations.append('DUPLICATE_RETRY_ALLOWED_WITHOUT_RECONCILIATION')
            return
        if typ in {'RECONCILED_ORDER_STATE','LATE_FILL_AFTER_DELAY','FILL_DURING_CANCEL','FULL_FILL_CONFIRMED','PARTIAL_FILL_CONFIRMED'}:
            if typ in {'RECONCILED_ORDER_STATE','LATE_FILL_AFTER_DELAY','FILL_DURING_CANCEL'}:
                self.reconciled=True
            if qty>0 and side in {'UP','DOWN'} and typ in {'LATE_FILL_AFTER_DELAY','FILL_DURING_CANCEL','FULL_FILL_CONFIRMED','PARTIAL_FILL_CONFIRMED'}:
                self.net += qty if side=='UP' else -qty
            if self.reconciled:
                self.uncertain=False
                if abs(self.net)>1e-9 and not self.passive:
                    self.passive=True; self.outstanding_passive=True
                    self.actions.append('PASSIVE_REPAIR_'+('DOWN' if self.net>0 else 'UP'))
            return
        if typ=='PASSIVE_REPAIR_FILL':
            if not self.passive: self.violations.append('PASSIVE_FILL_WITHOUT_PASSIVE_REPAIR')
            self.net += qty if side=='UP' else -qty
            self.outstanding_passive=False
            if abs(self.net)<=18+1e-9: self.frozen=False
            self.actions.append('PASSIVE_REPAIR_CONFIRMED')
            return
        if typ=='PASSIVE_REPAIR_STALL':
            if not self.outstanding_passive: self.violations.append('STALL_WITHOUT_OUTSTANDING_PASSIVE')
            if abs(self.net)>18+1e-9:
                self.active=True; self.actions.append('BOUNDED_ACTIVE_REPAIR_'+('DOWN' if self.net>0 else 'UP'))
            return
        if typ=='ACTIVE_REPAIR_FILL':
            if not self.active: self.violations.append('ACTIVE_FILL_WITHOUT_ESCALATION')
            self.net += qty if side=='UP' else -qty
            if abs(self.net)<=18+1e-9: self.frozen=False
            self.actions.append('ACTIVE_REPAIR_CONFIRMED')
            return


def run(name, events):
    r=Recovery()
    for e in events: r.event(*e)
    return {
      'scenario':name,'actions':r.actions,'ownerPreserved':r.owner_preserved,
      'duplicateRiskExposureAvoided':'SUPPRESS_DUPLICATE_RETRY' in r.actions,
      'reconciledFromConfirmedFill':r.reconciled,'passiveRepairActivated':r.passive,
      'boundedActiveEscalationActivated':r.active,'terminalNet':r.net,'terminalAbsNet':abs(r.net),
      'lifecycleInvariantViolations':r.violations,'pass':False
    }

def main():
    scenarios=[
      ('UNKNOWN_LATE_FILL_PASSIVE_SUCCESS',[
        ('ORDER_STATE_UNKNOWN',None,0),('ECONOMIC_RETRY_REQUEST','UP',18),
        ('RECONCILED_ORDER_STATE',None,0),('LATE_FILL_AFTER_DELAY','UP',36),
        ('PASSIVE_REPAIR_FILL','DOWN',18)
      ],False),
      ('UNKNOWN_LATE_FILL_PASSIVE_STALL_ACTIVE_SUCCESS',[
        ('ORDER_STATE_UNKNOWN',None,0),('ECONOMIC_RETRY_REQUEST','UP',18),
        ('RECONCILED_ORDER_STATE',None,0),('LATE_FILL_AFTER_DELAY','UP',36),
        ('PASSIVE_REPAIR_STALL',None,0),('ACTIVE_REPAIR_FILL','DOWN',18)
      ],True),
      ('CANCEL_TIMEOUT_FILL_DURING_CANCEL_ACTIVE_SUCCESS',[
        ('CANCEL_ACK_TIMEOUT',None,0),('ECONOMIC_RETRY_REQUEST','UP',18),
        ('RECONCILED_ORDER_STATE',None,0),('FILL_DURING_CANCEL','UP',36),
        ('PASSIVE_REPAIR_STALL',None,0),('ACTIVE_REPAIR_FILL','DOWN',18)
      ],True),
    ]
    rows=[]
    for name,events,expect_active in scenarios:
        row=run(name,events)
        row['expectedActiveEscalation']=expect_active
        row['pass']=(row['ownerPreserved'] and row['duplicateRiskExposureAvoided'] and row['reconciledFromConfirmedFill'] and row['passiveRepairActivated'] and row['boundedActiveEscalationActivated']==expect_active and row['terminalAbsNet']<=18+1e-9 and not row['lifecycleInvariantViolations'])
        rows.append(row)
    allpass=all(r['pass'] for r in rows)
    summary={
      'scenarios':len(rows),'passed':sum(r['pass'] for r in rows),'allPass':allpass,
      'duplicateRetrySuppressedAll':all(r['duplicateRiskExposureAvoided'] for r in rows),
      'reconciledAll':all(r['reconciledFromConfirmedFill'] for r in rows),
      'passiveRepairActivatedAll':all(r['passiveRepairActivated'] for r in rows),
      'activeEscalationCases':sum(r['boundedActiveEscalationActivated'] for r in rows),
      'zeroInvariantViolations':all(not r['lifecycleInvariantViolations'] for r in rows),
      'terminalAbsNetMax':max(r['terminalAbsNet'] for r in rows),
      'status':'TESTED_KEEP_SIGNAL' if allpass else 'TESTED_REJECTED'
    }
    rep={'testId':'HFT_R2_UNCERTAIN_ORDER_QUARANTINE_RECONCILE_V1','axis':'R2_AUTONOMOUS_REPAIR_UNCERTAIN_OWNERSHIP_QUARANTINE_RECONCILE','researchOnly':True,'performanceClaimAllowed':False,'summary':summary,'rows':rows,'interpretation':('Structural autonomous-repair gate passed: uncertain ownership suppresses duplicate retry, confirmed late fills rebase own-state, passive repair activates, and bounded active escalation occurs only after passive stall.' if allpass else 'Structural gate failed; do not carry this method forward.')}
    p=OUT/'hft_r2_uncertain_order_quarantine_reconcile_v1_report.json'; p.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
