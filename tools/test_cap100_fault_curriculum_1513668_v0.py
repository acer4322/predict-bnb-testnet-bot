from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
base=json.loads((ROOT/'data/research/execution_aware_fill_lifecycle_v0/cap100_min1_responsibility_accumulator_1513668_v0.json').read_text(encoding='utf-8'))
orders=base['orders']

# Structural fault-aware responsibility replay. No venue fill claims.
# Responsibility remains in same bucket after partial/reject/no-fill; ambiguous/cancel-unknown freezes new exposure.
def replay(fault):
    pending={k:0.0 for k in ['NORMAL_UP','NORMAL_DOWN','REPAIR_UP','REPAIR_DOWN']}
    events=[]; filled=[]; frozen=False
    fault_used=False
    target=set(fault.get('indices',[]))
    for i,o in enumerate(orders):
        bucket=o['bucket']; qty=float(o['shares']); price=float(o['price'])
        if frozen:
            events.append({'i':i,'action':'FREEZE_NEW_EXPOSURE','bucket':bucket})
            continue
        carry=pending[bucket]; desired=qty+carry; pending[bucket]=0.0
        mode=fault.get('type') if i in target else None
        if mode and (not fault.get('once') or not fault_used):
            fault_used=True
            if mode=='PARTIAL50':
                f=desired*0.5; rem=desired-f; filled.append((o['side'],price,f,bucket)); pending[bucket]+=rem
                events.append({'i':i,'fault':mode,'filled':f,'requeued':rem,'bucket':bucket})
            elif mode in {'REJECT','NO_FILL'}:
                pending[bucket]+=desired; events.append({'i':i,'fault':mode,'filled':0.0,'requeued':desired,'bucket':bucket})
            elif mode=='CANCEL_UNKNOWN':
                pending[bucket]+=desired; frozen=True; events.append({'i':i,'fault':mode,'action':'CANCEL_AND_RECONCILE','pending':desired,'bucket':bucket})
            continue
        filled.append((o['side'],price,desired,bucket)); events.append({'i':i,'action':'FILLED_OR_CARRIED','shares':desired,'bucket':bucket})
    escalations=[]
    for b,q in pending.items():
        if q<=1e-9: continue
        if b.startswith('REPAIR_'): escalations.append({'bucket':b,'pendingShares':q,'action':'ACTIVE_CONVERSION_REQUIRED'})
        else: escalations.append({'bucket':b,'pendingShares':q,'action':'CARRY_FORWARD_OR_TERMINAL_RISK_REDUCTION'})
    cost=sum(p*q for _,p,q,_ in filled); up=sum(q for s,_,q,_ in filled if s=='UP'); down=sum(q for s,_,q,_ in filled if s=='DOWN')
    pnl=up-cost
    return {'fault':fault,'filledOrders':len(filled),'cost':cost,'upShares':up,'downShares':down,'pnl':pnl,'frozen':frozen,'pending':pending,'escalations':escalations,'orphanResponsibilities':0 if all(e.get('action') for e in escalations) else len(escalations),'events':events}

cases=[
 {'name':'partial50_early_up','type':'PARTIAL50','indices':[0]},
 {'name':'reject_early_down','type':'REJECT','indices':[3]},
 {'name':'cancel_unknown_early','type':'CANCEL_UNKNOWN','indices':[3]},
 {'name':'two_consecutive_up_no_fill','type':'NO_FILL','indices':[0,1]},
 {'name':'repair_up_reject','type':'REJECT','indices':[11]},
]
out={'version':'CAP100_FAULT_CURRICULUM_1513668_V0','marketId':1513668,'baselinePnl':3.4476190476190425,'cases':[replay(c) for c in cases], 'guards':['Structural responsibility replay only; no HFT/venue fill probability claim.','PnL is frozen-order accounting sensitivity with known winner and is not promotion evidence.']}
path=ROOT/'data/research/execution_aware_fill_lifecycle_v0/cap100_fault_curriculum_1513668_v0.json';path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'out':str(path),'cases':[{k:v for k,v in x.items() if k not in {'events'}} for x in out['cases']]},ensure_ascii=False,indent=2))
