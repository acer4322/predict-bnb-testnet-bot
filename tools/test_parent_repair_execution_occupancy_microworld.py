from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.eth_repair_modular.parent_execution_occupancy import ParentRepairExecutionOccupancyV1

EPS=1e-9

def run():
    checks=[]
    led=ParentRepairExecutionOccupancyV1(); pid=7; debt=2.4390243902439024

    led.reserve(key='P1',parent_id=pid,route='PASSIVE',qty=0.50)
    checks.append(('passive_reservation_visible',abs(led.parent_reserved(pid)-0.50)<EPS))
    checks.append(('active_min_allowed_in_remaining',led.can_reserve(pid,debt,1.6949152542372883)))
    checks.append(('oversized_active_blocked',not led.can_reserve(pid,debt,2.0)))

    led.mark_cancel_pending('P1')
    checks.append(('cancel_pending_does_not_release',abs(led.parent_reserved(pid)-0.50)<EPS))
    led.confirm_terminal('P1',cumulative_fill=0.0)
    checks.append(('terminal_cancel_releases_unfilled',abs(led.parent_reserved(pid))<EPS))

    checks.append(('anchorless_active_capacity_exists',led.can_reserve(pid,debt,1.6949152542372883)))
    led.reserve(key='A1',parent_id=pid,route='ACTIVE',qty=1.6949152542372883)
    checks.append(('active_reservation_recorded',abs(led.parent_reserved(pid)-1.6949152542372883)<EPS))

    remaining=led.available(pid,debt)
    checks.append(('remaining_capacity_exact',abs(remaining-(debt-1.6949152542372883))<EPS))
    checks.append(('later_passive_full_debt_blocked',not led.can_reserve(pid,debt,debt)))
    checks.append(('later_passive_small_slice_allowed',led.can_reserve(pid,debt,remaining)))
    led.reserve(key='P2',parent_id=pid,route='PASSIVE',qty=remaining)
    checks.append(('joint_occupancy_bounded',led.parent_reserved(pid)<=debt+EPS))

    led.observe_fill('A1',1.0)
    checks.append(('confirmed_fill_reduces_unresolved_occupancy',abs(led.parent_reserved(pid)-(1.6949152542372883-1.0+remaining))<EPS))

    led.mark_cancel_pending('P2'); before=led.parent_reserved(pid)
    checks.append(('late_cancel_pending_still_owned',abs(led.parent_reserved(pid)-before)<EPS))
    led.confirm_terminal('P2',cumulative_fill=0.2)
    checks.append(('terminal_partial_cancel_releases_remainder',abs(led.parent_reserved(pid)-(1.6949152542372883-1.0))<EPS))

    entry={'parentId':pid,'submittedQty':remaining,'actualFilled':0.2,'cancelRequested':True,'terminalConfirmed':True,'lane':'PASSIVE_REPAIR'}
    led.sync_from_carrier('P2',entry)
    checks.append(('terminal_sync_idempotent',abs(led.parent_reserved(pid)-(1.6949152542372883-1.0))<EPS))

    led.reserve(key='X',parent_id=8,route='PASSIVE',qty=1.0)
    checks.append(('parent_scoped_isolation',abs(led.parent_reserved(pid)-(1.6949152542372883-1.0))<EPS and abs(led.parent_reserved(8)-1.0)<EPS))

    failed=[n for n,ok in checks if not ok]
    out={'version':'PARENT_REPAIR_EXECUTION_OCCUPANCY_MICROWORLD_V1','passed':len(checks)-len(failed),'total':len(checks),'failed':failed,'decision':'PASS_PARENT_SCOPED_OCCUPANCY_MICROWORLD' if not failed else 'FAIL_PARENT_SCOPED_OCCUPANCY_MICROWORLD','parent':led.describe_parent(pid,debt)}
    print(json.dumps(out,ensure_ascii=False))
    if failed: raise SystemExit(1)

if __name__=='__main__': run()
