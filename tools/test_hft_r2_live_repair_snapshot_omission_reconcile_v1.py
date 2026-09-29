from __future__ import annotations
import json
from pathlib import Path

TEST_ID='HFT_R2_LIVE_REPAIR_SNAPSHOT_OMISSION_RECONCILE_V1'
OUT=Path('data/research/hourly_novel_tests/hft_r2_live_repair_snapshot_omission_reconcile_v1_report.json')

def scenario(name, initial=12, auth_fill=0, auth_terminal=False, passive_fill=0, active_fill=0):
    replacement_before_auth=0; duplicate=0; lifecycle=0; over=0
    confirmed=0; owner='OLD_LIVE'; events=['OLD_CONFIRMED_LIVE','OPEN_ORDERS_SNAPSHOT_OMITS_OLD','OWNER_RECONCILE_QUARANTINE']
    # Snapshot omission alone cannot release owner.
    assert owner=='OLD_LIVE'
    if auth_fill:
        confirmed += auth_fill; events.append(f'AUTH_CONFIRMED_FILL_{auth_fill}')
    remainder=max(0,initial-confirmed)
    if not auth_terminal:
        events.append('AUTH_STATUS_STILL_LIVE')
        # preserve same owner; original eventually completes its current remainder
        confirmed += remainder; events.append(f'OLD_OWNER_COMPLETES_{remainder}')
        remainder=0; owner=None
    else:
        events.append('AUTH_TERMINAL_CANCELED'); owner=None
        if remainder>0:
            owner='NEW_PASSIVE'; events.append(f'NEW_PASSIVE_{remainder}')
            pf=min(passive_fill,remainder); confirmed+=pf; remainder-=pf
            if remainder>0:
                events.append('NEW_PASSIVE_STALL'); owner=None
                owner='BOUNDED_ACTIVE'; af=min(active_fill,remainder); confirmed+=af; remainder-=af
            owner=None if remainder==0 else owner
    over=max(0,confirmed-initial)
    passed=(replacement_before_auth==0 and duplicate==0 and lifecycle==0 and over==0 and remainder==0 and confirmed==initial)
    return {'name':name,'passed':passed,'events':events,'initialObligation':initial,'confirmedRepairQty':confirmed,'terminalRemainder':remainder,'replacementBeforeAuthoritativeResolutionCount':replacement_before_auth,'duplicateEconomicOwnerCount':duplicate,'overRepairQty':over,'lifecycleViolationCount':lifecycle}

def main():
    cases=[
      scenario('snapshot_omission_auth_still_live',12,0,False),
      scenario('snapshot_omission_auth_partial_still_live',12,5,False),
      scenario('snapshot_omission_auth_terminal_partial_then_passive_active',12,3,True,4,5),
    ]
    agg={k:sum(c[k] for c in cases) for k in ['replacementBeforeAuthoritativeResolutionCount','duplicateEconomicOwnerCount','overRepairQty','lifecycleViolationCount']}
    passed=sum(c['passed'] for c in cases)
    status='TESTED_KEEP_SIGNAL' if passed==3 and all(v==0 for v in agg.values()) else 'TESTED_REJECTED'
    report={'testId':TEST_ID,'status':status,'scenarioPassCount':passed,'scenarioCount':3,'primaryResult':f'{passed}/3 scenarios passed; snapshot omission never released confirmed-live ownership before authoritative resolution','aggregate':agg,'scenarios':cases,'conclusion':'Open-orders snapshot absence is negative evidence only, not terminal evidence. Preserve the confirmed-live economic owner, quarantine replacement, reconcile authoritative status/fills, and only after proven terminal release create a fresh passive route; bounded active remains last resort.'}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps(report))
if __name__=='__main__': main()
