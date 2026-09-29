from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONTRACT=json.loads((ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'total_policy_supervisor_contract_v0.json').read_text(encoding='utf-8'))

def decide(s):
    if s.get('terminal'): return 'TERMINAL_CLOSE'
    if s.get('cancel_unknown') or s.get('ambiguous_order'): return 'CANCEL_AND_RECONCILE'
    if s.get('forced_risk'): return 'FORCED_RISK_REDUCTION'
    if s.get('active_required'): return 'ACTIVE_CONVERSION'
    if s.get('stale_data') and s.get('has_exposure'): return 'FREEZE_NEW_EXPOSURE'
    if s.get('repair_needed'): return 'PASSIVE_REPAIR'
    if s.get('pending_fill'): return 'HOLD_FOR_CONFIRMED_FILL'
    return 'NORMAL_MAKER'

cases=[
 {'name':'normal','state':{}},
 {'name':'partial_fill_pending','state':{'pending_fill':True,'has_exposure':True}},
 {'name':'unresolved_required','state':{'active_required':True,'repair_needed':True,'has_exposure':True}},
 {'name':'ambiguous_live_order','state':{'ambiguous_order':True,'active_required':True,'has_exposure':True}},
 {'name':'cancel_unknown','state':{'cancel_unknown':True,'has_exposure':True}},
 {'name':'stale_with_exposure','state':{'stale_data':True,'has_exposure':True}},
 {'name':'repair_only','state':{'repair_needed':True,'has_exposure':True}},
 {'name':'forced_risk','state':{'forced_risk':True,'active_required':True,'has_exposure':True}},
 {'name':'terminal','state':{'terminal':True,'cancel_unknown':True,'has_exposure':True}},
]
rows=[]
allowed=set(CONTRACT['actionClasses'])
for c in cases:
    action=decide(c['state']); rows.append({'name':c['name'],'action':action,'legal':action in allowed})
assert all(r['legal'] for r in rows)
assert dict((r['name'],r['action']) for r in rows)['unresolved_required']=='ACTIVE_CONVERSION'
assert dict((r['name'],r['action']) for r in rows)['ambiguous_live_order']=='CANCEL_AND_RECONCILE'
assert dict((r['name'],r['action']) for r in rows)['stale_with_exposure']=='FREEZE_NEW_EXPOSURE'
print(json.dumps({'version':'TOTAL_POLICY_SUPERVISOR_V0_FAULT_SMOKE','cases':rows,'allReachableCasesMapped':True},ensure_ascii=False,indent=2))
