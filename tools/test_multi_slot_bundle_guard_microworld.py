from __future__ import annotations
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.multi_slot_bundle_guard import *

P=MultiSlotBundleRecoverabilityPolicyV1()
cases=[]
def add(name,ctx,expect,reason=None):
 d=P.evaluate(ctx);ok=(d.recoverable is expect) and (reason is None or d.reason==reason);cases.append({'name':name,'pass':ok,'decision':d.__dict__})

add('ONE_MIN_LEGAL_RECOVERABLE',MultiSlotBundleRecoverabilityContext('UP',-0.2,10,9.5,9.7,(PendingExpandLeg(2.0,0.5),),(),0.45),True)
add('THREE_SIBLINGS_CAN_BECOME_UNRECOVERABLE',MultiSlotBundleRecoverabilityContext('UP',-0.2,10,9.5,9.7,(PendingExpandLeg(2.0,0.5),PendingExpandLeg(2.0,0.5),PendingExpandLeg(2.0,0.5)),(),0.8,3.0),False)
add('OWNED_REPAIR_CAN_COVER_BUNDLE',MultiSlotBundleRecoverabilityContext('UP',-0.4,10,9,9.8,(PendingExpandLeg(2.0,0.5),),(OwnedRepairLeg(2.3,0.1),),0.4),True,'EXISTING_REPAIR_RESERVATION_COVERS_BUNDLE')
add('INVALID_EXPAND_LEG_REJECTED',MultiSlotBundleRecoverabilityContext('UP',0,1,1,1,(PendingExpandLeg(1.0,1.0),),(),0.5),False,'INVALID_PENDING_EXPAND_LEG')
add('INVALID_SIDE_REJECTED',MultiSlotBundleRecoverabilityContext('X',0,1,1,1,(),(),0.5),False,'INVALID_THESIS_SIDE')
out={'version':'MULTI_SLOT_BUNDLE_GUARD_MICROWORLD_V1','passed':sum(x['pass'] for x in cases),'total':len(cases),'cases':cases,'decision':'PASS' if all(x['pass'] for x in cases) else 'FAIL'}
print(json.dumps(out,ensure_ascii=False))
if out['decision']!='PASS':raise SystemExit(1)
