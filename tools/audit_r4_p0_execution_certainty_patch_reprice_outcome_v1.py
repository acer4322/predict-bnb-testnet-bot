from pathlib import Path
p=Path(__file__).with_name('audit_r4_p0_execution_certainty_v1.py')
s=p.read_text(encoding='utf-8')
old="""    for parent,reqinfo in reprice_requests.items():
        pz=intents.get(parent)
        if not pz: continue
        children=reprice_children.get(parent) or []
        if pz.get('terminal')=='ACK_CANCELED' and actual_confirmed+EPS<root_req and not children:
            missing=max(0.0,root_req-actual_confirmed)
            fake={'received_at_ms':reqinfo['receivedAtMs'],'event_type':'REPRICE_EXPECTED_AFTER_ACK_CANCELED','intent_id':parent}
            flag('REPRICE_REINSERT_MISSING_AFTER_CANCEL',fake,missing,{'repricePx':reqinfo['repricePx'],'parentTerminal':pz.get('terminal')})
        if children:
            counts['REPRICE_REINSERT_OBSERVED']+=len(children)
"""
new="""    for parent,reqinfo in reprice_requests.items():
        pz=intents.get(parent)
        if not pz: continue
        children=reprice_children.get(parent) or []
        if children:
            counts['REPRICE_REINSERT_OBSERVED']+=len(children)
            counts['REPRICE_REQUEST_OUTCOME_REINSERTED']+=1
        elif pz.get('phase')=='FILLED' or pz.get('filled',0.0)+EPS>=pz.get('requested',0.0):
            counts['REPRICE_REQUEST_OUTCOME_PARENT_FILLED']+=1
            qty['REPRICE_REQUEST_OUTCOME_PARENT_FILLED']+=float(pz.get('filled',0.0))
        elif pz.get('terminal')=='ACK_CANCELED' and actual_confirmed+EPS>=root_req:
            counts['REPRICE_REQUEST_OUTCOME_ROOT_COMPLETED_ELSEWHERE']+=1
        elif pz.get('terminal')=='ACK_CANCELED' and actual_confirmed+EPS<root_req:
            missing=max(0.0,root_req-actual_confirmed)
            fake={'received_at_ms':reqinfo['receivedAtMs'],'event_type':'REPRICE_EXPECTED_AFTER_ACK_CANCELED','intent_id':parent}
            flag('REPRICE_REINSERT_MISSING_AFTER_CANCEL',fake,missing,{'repricePx':reqinfo['repricePx'],'parentTerminal':pz.get('terminal')})
        elif root_terminated:
            counts['REPRICE_REQUEST_OUTCOME_MARKET_TERMINATED']+=1
        else:
            counts['REPRICE_REQUEST_OUTCOME_UNRESOLVED']+=1
"""
if old not in s:
    raise SystemExit('target block not found')
s=s.replace(old,new)
p.write_text(s,encoding='utf-8')
print('patched outcome',p)
