from __future__ import annotations
import collections,json,statistics
from pathlib import Path
SRC=Path('data/research/lan_worker_returns/eth-alignment-residual-reexpand-shadow-1912961-20260904-v1/result.json')
OUT=Path('data/research/r4_v0/behavior_alignment_v1/RECURSIVE_COMPOSITE_RECOVERABILITY_SHADOW_1912961_RESULT_20260904.json')
EPS=1e-9

def simulate(row,max_steps=6):
    p=row['prospective']; floor=float(p['floorAfterExpand']); target=float(p['floorBefore']); debt=float(p['repairDebtAfterHypExpand']); debt_side=str(p['expandSide'])
    prices={str(p['expandSide']):float(p['expandPrice']),str(p['repairSide']):float(p['repairPrice'])}
    path=[]; max_debt=debt
    for step in range(1,max_steps+1):
        pay='DOWN' if debt_side=='UP' else 'UP'; px=prices[pay]
        if not (EPS<px<1.0-EPS): return {'recoveredStep':None,'path':path,'badPrice':True,'maxDebt':max_debt}
        q=1.0/px; repair=min(debt,q); overflow=max(0.0,q-debt)
        floor=floor + repair*(1.0-px) - overflow*px
        path.append({'step':step,'paySide':pay,'price':px,'physicalQty':q,'repairAllocation':repair,'overflowAllocation':overflow,'floor':floor})
        if floor>=target-EPS:
            return {'recoveredStep':step,'path':path,'maxDebt':max_debt,'terminalDebt':overflow}
        if overflow>EPS:
            debt=overflow; debt_side=pay
        else:
            debt=max(0.0,debt-q)
        max_debt=max(max_debt,debt)
        if debt<=EPS: break
    return {'recoveredStep':None,'path':path,'maxDebt':max_debt,'terminalDebt':debt}

d=json.loads(SRC.read_text(encoding='utf-8'))
rows=[r for r in d['rows'] if float(r.get('secondsLeft') or 0)>180 and r.get('classification')=='CURRENT_RECOVERABILITY_REJECT' and isinstance(r.get('prospective'),dict) and r['prospective'].get('repairPrice') is not None]
results=[]
for r in rows:
    s=simulate(r,6); ps=float(r['prospective']['expandPrice'])+float(r['prospective']['repairPrice'])
    results.append({'t':r['t'],'secondsLeft':r['secondsLeft'],'pExpand':r.get('pExpand'),'legacyReason':(r.get('currentRecoverability') or {}).get('reason'),'pairSumAtClock':ps,'floorBefore':r['prospective']['floorBefore'],'floorAfterExpand':r['prospective']['floorAfterExpand'],'recoveredStep':s['recoveredStep'],'maxDebt':s.get('maxDebt'),'path':s['path']})
steps=collections.Counter(str(x['recoveredStep']) if x['recoveredStep'] is not None else 'NONE' for x in results)
by_reason={}
for reason in sorted(set(x['legacyReason'] for x in results)):
    z=[x for x in results if x['legacyReason']==reason]
    by_reason[reason]={'n':len(z),'recoveredWithin6':sum(x['recoveredStep'] is not None for x in z),'pairSumLt1':sum(x['pairSumAtClock']<1.0-EPS for x in z)}
pairs=[x['pairSumAtClock'] for x in results]
recovered=[x for x in results if x['recoveredStep'] is not None]
out={'version':'RECURSIVE_COMPOSITE_RECOVERABILITY_SHADOW_1912961_RESULT','date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,'marketId':1912961,'method':'At each post-V90D pre-180 strict-past clock, hold the currently visible two-side bid prices fixed and simulate up to six alternating venue-min physical carriers. Each carrier allocates Repair-first and overflow-second. No future quote/action/settlement is used. This is a structural reachability shadow, not a runtime policy.','summary':{'clocks':len(results),'recoveredWithin6':len(recovered),'recoveredShare':len(recovered)/len(results) if results else 0.0,'recoveredStepCounts':dict(steps),'pairSumMin':min(pairs) if pairs else None,'pairSumMedian':statistics.median(pairs) if pairs else None,'pairSumMax':max(pairs) if pairs else None,'pairSumLt1':sum(x<1.0-EPS for x in pairs),'byLegacyReason':by_reason},'examples':recovered[:20],'decision':'PREREGISTER_RECURSIVE_COMPOSITE_RECOVERABILITY_KERNEL_SHADOW' if recovered else 'NO_CURRENT_QUOTE_RECURSIVE_RECOVERABILITY_SUPPORT','boundary':['strict-past current quotes only','fixed-price structural counterfactual; not future fill guarantee','Repair-first overflow-second conservation','no behavior change','no Target runtime input','<=180s speculative fence unchanged','no 8781']}
OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'summary':out['summary'],'decision':out['decision']},ensure_ascii=False,indent=2))
