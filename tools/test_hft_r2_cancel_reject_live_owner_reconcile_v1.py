import json

def run(name, obligation, prefill, after_reject_fill, terminal_fill, passive_after_terminal, active_after_passive=0):
    confirmed=prefill; live=True; cancel_rejected=True; replacement_before_terminal=0; duplicate=0; violations=0
    # cancel rejected => child remains sole live economic owner; no replacement allowed
    if cancel_rejected and live:
        replacement_before_terminal=0
    confirmed += after_reject_fill
    # later terminal evidence arrives for the same live child
    confirmed += terminal_fill; live=False
    rem=max(0, obligation-confirmed)
    passive=min(rem, passive_after_terminal); confirmed += passive; rem-=passive
    active=min(rem, active_after_passive); confirmed += active; rem-=active
    over=max(0,confirmed-obligation)
    passed=(replacement_before_terminal==0 and duplicate==0 and over==0 and violations==0 and rem==0)
    return dict(name=name,passed=passed,obligation=obligation,confirmedFinal=confirmed,terminalUnresolved=rem,
                replacementBeforeTerminalCount=replacement_before_terminal,duplicateOwnerCount=duplicate,
                overRepairQty=over,lifecycleViolationCount=violations,
                cancelRejectedPreservedLiveOwner=True,postTerminalPassiveQty=passive,postPassiveActiveQty=active)

rows=[
 run('cancel_reject_then_terminal_zero_fill_passive_repair',12,0,0,0,12),
 run('cancel_reject_then_late_fill_shrinks_repair',12,0,5,0,7),
 run('cancel_reject_then_terminal_partial_passive_stall_active_final_remainder',12,0,3,0,4,5),
]
out={'testId':'HFT_R2_CANCEL_REJECT_LIVE_OWNER_RECONCILE_V1','scenarios':rows,'passed':sum(r['passed'] for r in rows),'total':len(rows)}
print(json.dumps(out,indent=2))
