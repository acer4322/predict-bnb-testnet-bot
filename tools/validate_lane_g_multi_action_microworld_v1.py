from __future__ import annotations
import argparse,json,math
from pathlib import Path
EPS=1e-9
ACTIONS=['KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE']

def payoff(u,d,c):
    b=max(u,d)-c;f=min(u,d)-c
    return {'upQty':u,'downQty':d,'cost':c,'best':b,'floor':f,'gap':b-f}

def apply_fill(state,side,price,qty):
    s=dict(state);s['upQty']=float(s['upQty']);s['downQty']=float(s['downQty']);s['cost']=float(s['cost'])
    s['cost']+=float(price)*float(qty)
    s[str(side).lower()+'Qty']+=float(qty)
    return payoff(s['upQty'],s['downQty'],s['cost'])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--anchors',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    doc=json.loads(Path(a.anchors).read_text(encoding='utf-8'));rows=[]
    for mid,m in doc['markets'].items():
        spec=m['spec'];initial=m['initialPayoff']
        for action in ACTIONS:
            br=m['branches'][action];ev=br.get('firstStructuralEvent') or {};pred=payoff(float(initial['upQty']),float(initial['downQty']),float(initial['cost']))
            branch_key=br.get('branchKey')
            for r in ev.get('reasons') or []:
                if r.get('type')!='CONFIRMED_INVOLVED_FILL':continue
                key=str(r.get('key'));q=float(r.get('fillDelta') or 0.0)
                if key==str(spec['siblingKey']):side=spec['repairSide'];price=float(spec['siblingPrice'])
                elif branch_key and key==str(branch_key):
                    if action=='ORDINARY_REEXPAND':side=spec['ordinary']['side'];price=float(spec['ordinary']['price'])
                    elif action=='R303_CONTINGENT_COMPOSITE':side=spec['composite']['side'];price=float(spec['composite']['price'])
                    else:raise AssertionError((mid,action,key))
                else:raise AssertionError(f'unmapped involved key {mid} {action} {key} branch={branch_key}')
                pred=apply_fill(pred,side,price,q)
            exact=ev.get('payoff') or initial
            errs={k:abs(float(pred[k])-float(exact[k])) for k in ['upQty','downQty','cost','best','floor','gap']}
            ok=max(errs.values())<=1e-8
            rows.append({'marketId':int(mid),'action':action,'passed':ok,'predicted':pred,'exact':exact,'errors':errs,'eventReasons':ev.get('reasons') or []})
            print(json.dumps({'marketId':int(mid),'action':action,'passed':ok,'maxError':max(errs.values())},ensure_ascii=False),flush=True)
    gates={'rows15':len(rows)==15,'allExactPhysicalPayoffReproduced':all(x['passed'] for x in rows),'maxAbsError':max(max(x['errors'].values()) for x in rows) if rows else None}
    out={'version':'LANE_G_MULTI_ACTION_MICROWORLD_V1_EXACT_EVENT_CALIBRATION_20260907','researchOnly':True,'runtimeAuthority':False,'anchors':a.anchors,'rows':rows,'gates':gates,'calibrationPass':bool(gates['rows15'] and gates['allExactPhysicalPayoffReproduced']),'boundary':['first structural event only','physical inventory/cost transformation only','no fill probability assumption','no scalar reward','consumed exact-fork anchors only','no fresh/no 8781']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':gates},ensure_ascii=False))
if __name__=='__main__':main()
