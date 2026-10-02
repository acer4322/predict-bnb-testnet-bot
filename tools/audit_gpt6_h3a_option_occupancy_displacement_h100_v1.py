"""Read-only H3a option-occupancy displacement audit over completed H100 breadth forks."""
from __future__ import annotations
import argparse, collections, json, math
from pathlib import Path

def qlabel(df,db,eps=1e-9):
    if df>eps and db>eps:return 'PARETO_POSITIVE'
    if df<-eps and db>eps:return 'AMPLIFY_RISK'
    if df>eps and db<-eps:return 'DEFENSIVE'
    if df<-eps and db<-eps:return 'PARETO_NEGATIVE'
    return 'MIXED_OR_NEUTRAL'

def ev_kinds(events): return tuple(str(x.get('kind')) for x in (events or []))

def match_role(detail,state):
    side=str(detail.get('side') or '')
    p=float(detail.get('price') or 0.0)
    q=float(detail.get('qty') or 0.0)
    for o in (state or {}).get('liveOrders') or []:
        if str(o.get('side') or '')!=side: continue
        if abs(float(o.get('price') or 0.0)-p)>1e-8: continue
        if abs(float(o.get('qty') or 0.0)-q)>1e-7: continue
        return str(o.get('role') or detail.get('role') or 'UNASSIGNED')
    return str(detail.get('role') or 'UNASSIGNED')

def submit_sigs(events,state):
    out=[]
    for e in events or []:
        if e.get('kind')!='NEW_SUBMIT': continue
        det=e.get('details') or []
        if isinstance(det,dict): det=[det]
        for x in det:
            out.append({'side':str(x.get('side') or ''),'role':match_role(x,state),'price':round(float(x.get('price') or 0.0),10),'qty':round(float(x.get('qty') or 0.0),10)})
    return out

def cancel_sigs(events):
    out=[]
    for e in events or []:
        if e.get('kind')!='CANCEL_REQUEST': continue
        x=e.get('details') or {}
        out.append({'side':str(x.get('side') or ''),'role':str(x.get('role') or ''),'price':round(float(x.get('price') or 0.0),10),'reason':str(x.get('reason') or '')})
    return out

def archetype(fd):
    if fd is None:return 'NO_FIRST_DIVERGENCE'
    L,R=fd.get('leftEvents') or [],fd.get('rightEvents') or []
    lk, rk = ev_kinds(L), ev_kinds(R)
    lnew=sum(k=='NEW_SUBMIT' for k in lk); rnew=sum(k=='NEW_SUBMIT' for k in rk)
    lcan=sum(k=='CANCEL_REQUEST' for k in lk); rcan=sum(k=='CANCEL_REQUEST' for k in rk)
    lfill=sum(k=='FILL' for k in lk); rfill=sum(k=='FILL' for k in rk)
    lrel=sum(k=='SLOT_RELEASE' for k in lk); rrel=sum(k=='SLOT_RELEASE' for k in rk)
    if lnew and not rnew:return 'ZERO_NEW_SUBMIT_ONLY'
    if rnew and not lnew:return 'UNARMED_NEW_SUBMIT_ONLY'
    if lnew and rnew:return 'BOTH_NEW_SUBMIT'
    if lcan!=rcan:return 'CANCEL_ASYMMETRY'
    if lfill!=rfill:return 'FILL_ASYMMETRY'
    if lrel!=rrel:return 'SLOT_RELEASE_ASYMMETRY'
    return 'OTHER_EVENT_COMPOSITION'

def action_family(submits):
    if not submits:return 'NONE'
    fam=[]
    for s in submits:
        role=s['role']
        if 'EXPAND' in role: x='EXPAND'
        elif 'REPAIR' in role or role=='ECONOMIC_CORE': x='REPAIR_OR_CORE'
        else:x=role or 'UNASSIGNED'
        fam.append(f"{x}:{s['side']}")
    return '+'.join(fam)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--states',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.loads(Path(a.input).read_text(encoding='utf-8')); st=json.loads(Path(a.states).read_text(encoding='utf-8')); sm={int(x['marketId']):x for x in st['states']}
    rows=[]
    for r in d['rows']:
        fd=r.get('firstDivergence'); L=(fd or {}).get('leftEvents') or []; R=(fd or {}).get('rightEvents') or []
        ls=submit_sigs(L,(fd or {}).get('leftState')); rs=submit_sigs(R,(fd or {}).get('rightState'))
        de=r['deltaTerminal']; s=sm[int(r['marketId'])]
        row={'marketId':int(r['marketId']),'classification':r['classification'],'sameDirect':bool(r['sameDirectInterventionSignature']),'archetype':archetype(fd),'eventKindSig':{'ZERO':list(ev_kinds(L)),'UNARMED':list(ev_kinds(R))},'zeroNewSubmits':ls,'unarmedNewSubmits':rs,'zeroActionFamily':action_family(ls),'unarmedActionFamily':action_family(rs),'zeroCancels':cancel_sigs(L),'unarmedCancels':cancel_sigs(R),'lagMs':None if fd is None else int(fd.get('lagMs')),'effectQuality':qlabel(float(de['floor']),float(de['best'])),'deltaFloor':float(de['floor']),'deltaBest':float(de['best']),'deltaGap':float(de['gap']),'deltaFills':float(de['fills']),'deltaActiveSubmits':float(de['activeSubmits']),'prefix':{'repairProgressFrac':float(s['repairProgressFrac']),'floor':float(s['floor']),'best':float(s['best']),'remainingDebtQty':float(s['remainingDebtQty']),'liveSlots':int(s['liveSlots']),'liveRepairSlots':int(s['liveRepairSlots']),'liveExpandSlots':int(s['liveExpandSlots']),'freeSlots':int(s['freeSlots']),'imbalance':float(s['book']['imbalance']),'spread':float(s['book']['spread']),'weakSide':str(s['weakSide']),'repairPrice':float(s['h3aFrozenCarrier']['price'])}}
        rows.append(row)
    clean=[x for x in rows if x['classification']=='CLEAN_PHYSICAL_OCCUPANCY_SUFFIX_EFFECT']
    def count(key,xs):return dict(collections.Counter(x[key] for x in xs))
    pair_counts=collections.Counter((x['zeroActionFamily'],x['unarmedActionFamily']) for x in clean)
    quality_by_arch={}
    for arch in sorted({x['archetype'] for x in clean}):
        xs=[x for x in clean if x['archetype']==arch]
        quality_by_arch[arch]={'n':len(xs),'qualityCounts':dict(collections.Counter(x['effectQuality'] for x in xs)),'meanDeltaFloor':sum(x['deltaFloor'] for x in xs)/len(xs),'meanDeltaBest':sum(x['deltaBest'] for x in xs)/len(xs),'meanDeltaGap':sum(x['deltaGap'] for x in xs)/len(xs)}
    quality_by_pair={}
    for pair,n in pair_counts.items():
        xs=[x for x in clean if (x['zeroActionFamily'],x['unarmedActionFamily'])==pair]
        quality_by_pair[f'{pair[0]} -> {pair[1]}']={'n':n,'qualityCounts':dict(collections.Counter(x['effectQuality'] for x in xs)),'meanDeltaFloor':sum(x['deltaFloor'] for x in xs)/n,'meanDeltaBest':sum(x['deltaBest'] for x in xs)/n}
    out={'version':'GPT6_H3A_OPTION_OCCUPANCY_DISPLACEMENT_AUDIT_H100_V1_20260907','researchOnly':True,'runtimeAuthority':False,'source':a.input,'rows':rows,'summary':{'n':len(rows),'cleanN':len(clean),'allArchetypeCounts':count('archetype',rows),'cleanArchetypeCounts':count('archetype',clean),'cleanActionFamilyTransitions':{f'{a} -> {b}':n for (a,b),n in pair_counts.items()},'cleanQualityCounts':count('effectQuality',clean),'qualityByArchetype':quality_by_arch,'qualityByActionFamilyTransition':quality_by_pair},'boundary':['mediator extraction uses first divergence events only','effect-quality labels are posthoc diagnostic','no new replay/no runtime authority/no fixed-time policy rule/no NEW24-B/no 8781']}
    Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
