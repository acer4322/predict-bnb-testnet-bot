from __future__ import annotations
import json, math
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/r4_v0/p0_provenance_v1'
H3D=BASE/'GPT6_H3D_RECOGNITION_ACTION_VALUE_SELECTOR_DEV41_RESULT_20260907.json'
RESULT_FILES=[
 BASE/'GPT6_H3C_CORE_RESERVATION_RECOGNITION_TIMING_SMOKE5_RESULT_20260907.json',
 BASE/'GPT6_H3C_CORE_RESERVATION_RECOGNITION_STAGEB16_RESULT_20260907.json',
 BASE/'GPT6_H3C_RECOGNITION_ACTION_VALUE_DEV20_RESULT_20260907.json',
]
OUT=BASE/'GPT6_H3F_FIRST_DISPLACED_ACTION_LOCAL_VALUE_MEDIATION_DEV41_RESULT_20260907.json'
EPS=1e-9

def f(x):
    try:return float(x)
    except:return 0.0

def terminal_pnl(t,w):
    return f(t['upQty'] if w=='UP' else t['downQty'])-f(t['buyNotional'])

def candidate_vector(state,cand):
    if not cand:return None
    u=f(state.get('upQty')); d=f(state.get('downQty')); cost=f(state.get('cost'))
    side=str(cand.get('side')); p=f(cand.get('price')); q=f(cand.get('qty'))
    if side=='UP': u+=q; cost+=p*q
    elif side=='DOWN': d+=q; cost+=p*q
    else:return None
    up=u-cost; dn=d-cost
    return {'floor':min(up,dn),'best':max(up,dn),'midpoint':0.5*(up+dn),'upPayoff':up,'downPayoff':dn,
            'side':side,'relative':cand.get('relative'),'price':p,'qty':q}

def better(metric,L,R,terminal_better):
    if L is None or R is None:return None
    a=f(L[metric]); b=f(R[metric])
    if abs(a-b)<=EPS:return None
    pred='ON_FILL' if b>a else 'IMMEDIATE'
    return pred==terminal_better

def main():
    h3d=json.loads(H3D.read_text(encoding='utf-8'))
    labels={int(r['marketId']):r for r in h3d['rows']}
    rr={}
    for p in RESULT_FILES:
        for r in json.loads(p.read_text(encoding='utf-8'))['rows']: rr[int(r['marketId'])]=r
    rows=[]; topo={}
    for mid in sorted(rr):
        r=rr[mid]; lab=labels[mid]; comp=r['immediateVsOnFill']; fd=comp.get('firstDivergence')
        ln=comp.get('leftNextSubmit'); rn=comp.get('rightNextSubmit')
        pat=(str((ln or {}).get('relative') or 'NONE'),str((rn or {}).get('relative') or 'NONE')); topo[pat]=topo.get(pat,0)+1
        state=None
        if fd:
            state=fd.get('leftState') or fd.get('rightState')
        lv=candidate_vector(state,ln) if state else None; rv=candidate_vector(state,rn) if state else None
        pi=f(lab['pnlImmediate']); pf=f(lab['pnlOnFill']); dp=pf-pi
        material=abs(dp)>EPS; tb='ON_FILL' if dp>EPS else ('IMMEDIATE' if dp<-EPS else 'EQUAL')
        rows.append({'marketId':mid,'material':material,'terminalBetter':tb,'deltaPnlOnFillMinusImmediate':dp,
                     'classification':comp.get('classification'),'topology':{'immediateNext':pat[0],'onFillNext':pat[1]},
                     'immediateCandidateLocal':lv,'onFillCandidateLocal':rv,
                     'higherLocalFloorMatchesTerminal':better('floor',lv,rv,tb) if material else None,
                     'higherLocalBestMatchesTerminal':better('best',lv,rv,tb) if material else None,
                     'higherLocalMidpointMatchesTerminal':better('midpoint',lv,rv,tb) if material else None})
    summary={}
    for key in ('higherLocalFloorMatchesTerminal','higherLocalBestMatchesTerminal','higherLocalMidpointMatchesTerminal'):
        vals=[x[key] for x in rows if x['material'] and x[key] is not None]
        summary[key]={'comparable':len(vals),'correct':sum(v is True for v in vals),'accuracy':(sum(v is True for v in vals)/len(vals) if vals else None)}
    material=[x for x in rows if x['material']]
    payload={'version':'GPT6_H3F_FIRST_DISPLACED_ACTION_LOCAL_VALUE_MEDIATION_DEV41_V1_20260907','researchOnly':True,'runtimeAuthority':False,
             'n':len(rows),'materialN':len(material),'topologyCounts':{f'{a}->{b}':n for (a,b),n in sorted(topo.items())},
             'summary':summary,'rows':rows,
             'decision':'SUPPORT_ONE_STEP_LOCAL_MEDIATOR' if any(v['comparable']>=10 and v['accuracy']>=0.70 for v in summary.values()) else 'ONE_STEP_LOCAL_MEDIATOR_NOT_SUPPORTED',
             'boundary':['development mediation diagnostic only','successor action is branch-generated future relative to original recognition decision; never runtime input','no sealed Holdout25','no classifier/no threshold scan']}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'decision':payload['decision'],'materialN':payload['materialN'],'topologyCounts':payload['topologyCounts'],'summary':summary},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
