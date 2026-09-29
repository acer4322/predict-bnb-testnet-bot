from __future__ import annotations
import json, math
from pathlib import Path
from collections import defaultdict, Counter
EPS=1e-9
ROOT=Path(__file__).resolve().parents[1]
JOBS=[
'eth-ms4-r213-full24-s1-20260906-v1',
'eth-ms4-r213-full24-s2-20260906-v1',
'eth-ms4-r213-full24-s3-20260906-v1',
'eth-ms4-r213-full24-s4-20260906-v1']

def fifo_pair(row):
    active_keys={str(e.get('key')) for e in row.get('ms4R2ExecutionDecisions',[]) if e.get('event')=='MS4_R2_ACTIVE_REPAIR_SUBMIT' and e.get('key')}
    fanout_keys={str(e.get('key')) for e in row.get('r26Events',[]) if e.get('event')=='PARALLEL_PASSIVE_REPAIR_FANOUT_SUBMIT' and e.get('key')}
    econ_keys={str(e.get('key')) for e in row.get('r213Events',[]) if e.get('event')=='ACTIVE_ECONOMIC_PASSIVE_REMAINDER_SUBMIT' and e.get('key')}
    evs=[e for e in row.get('splitEvents',[]) if e.get('event')=='ROLE_FILL_SPLIT' and float(e.get('fillInc') or 0)>EPS]
    evs=sorted(evs,key=lambda e:(int(e.get('t') or 0),str(e.get('key') or '')))
    un={'UP':[],'DOWN':[]}; pairs=[]
    role=defaultdict(lambda:{'qty':0.0,'num':0.0})
    for e in evs:
        side=str(e.get('side')); opp='DOWN' if side=='UP' else 'UP'; rem=float(e['fillInc']); p=float(e['price']); key=str(e.get('key') or ''); r=str(e.get('role') or 'UNKNOWN')
        if key in active_keys:r='ACTIVE_REPAIR'
        elif key in econ_keys:r='ECONOMIC_REMAINDER'
        elif key in fanout_keys:r='FANOUT_REPAIR'
        elif r=='SATELLITE_REPAIR':r='NATIVE_SATELLITE_REPAIR'
        while rem>EPS and un[opp]:
            lot=un[opp][0]; q=min(rem,lot['qty']); ps=p+lot['price']; pairs.append((q,ps,lot['role'],r)); role[r]['qty']+=q; role[r]['num']+=q*ps
            rem-=q;lot['qty']-=q
            if lot['qty']<=EPS:un[opp].pop(0)
        if rem>EPS:un[side].append({'qty':rem,'price':p,'role':r})
    pq=sum(q for q,_,_,_ in pairs); reserve=sum(q*max(0,1-ps) for q,ps,_,_ in pairs); debt=sum(q*max(0,ps-1) for q,ps,_,_ in pairs)
    return {'pairedQty':pq,'weightedPairSum':sum(q*ps for q,ps,_,_ in pairs)/pq if pq else None,'pairReserve':reserve,'pairDebt':debt,'netPairEdge':reserve-debt,
            'favorableShare':sum(q for q,ps,_,_ in pairs if ps<1-EPS)/pq if pq else None,'expensiveShare':sum(q for q,ps,_,_ in pairs if ps>1+EPS)/pq if pq else None,
            'unmatchedQty':sum(l['qty'] for s in un.values() for l in s),
            'rolePair':{k:{'qty':v['qty'],'weightedPairSum':v['num']/v['qty'] if v['qty'] else None} for k,v in role.items()}}

def attr(row,eco):
    pnl=float(row.get('pnlDiagnosticOnly') or 0); floor=float(row.get('floor') or 0); fills=int(row.get('fillEvents') or 0); submits=int(row.get('submits') or 0)
    rs=row.get('roleFillQty') or {}; active=float((eco.get('rolePair') or {}).get('ACTIVE_REPAIR',{}).get('qty') or 0); sat=float((eco.get('rolePair') or {}).get('NATIVE_SATELLITE_REPAIR',{}).get('qty') or 0)+float((eco.get('rolePair') or {}).get('FANOUT_REPAIR',{}).get('qty') or 0)
    core=float(rs.get('ECONOMIC_CORE',0) or 0); exp=float(rs.get('SATELLITE_EXPAND',0) or 0)
    pair=eco.get('weightedPairSum'); net=eco.get('netPairEdge',0); unmatched=eco.get('unmatchedQty',0)
    births=int(row.get('scopeBirths') or 0); comps=int(row.get('scopeCompletions') or 0); flips=int(row.get('scopeFlips') or 0)
    active_ps=(eco.get('rolePair') or {}).get('ACTIVE_REPAIR',{}).get('weightedPairSum')
    sat_roles=[(eco.get('rolePair') or {}).get(x,{}) for x in ('NATIVE_SATELLITE_REPAIR','FANOUT_REPAIR','ECONOMIC_REMAINDER')]; sat_q=sum(float(x.get('qty') or 0) for x in sat_roles); sat_ps=(sum(float(x.get('qty') or 0)*float(x.get('weightedPairSum') or 0) for x in sat_roles)/sat_q) if sat_q>EPS else None
    causes=[]; evidence=[]
    if float(row.get('unauthorizedOverflowQty',0) or 0)>EPS or float(row.get('repairQuotaExcessMax',0) or 0)>EPS:
        causes.append('EXECUTION_OR_ACCOUNTING_ANOMALY');evidence.append('correctness violation')
    if pnl<0:
        if active_ps is not None and active_ps>1.02 and active>0:
            causes.append('ACTIVE_REPAIR_PREMIUM_TOO_HIGH');evidence.append(f'activePairSum={active_ps:.4f}, activeQty={active:.3f}')
        if sat_ps is not None and sat_ps>1.02 and sat>0:
            causes.append('EXPENSIVE_PASSIVE_REPAIR');evidence.append(f'satellitePairSum={sat_ps:.4f}, satQty={sat:.3f}')
        if pair is not None and pair>1.0+EPS and net<0:
            causes.append('OVER_REPAIR_OR_REPAIR_DENSITY');evidence.append(f'weightedPairSum={pair:.4f}, netPairEdge={net:.3f}')
        if unmatched>1.0 and births>comps:
            causes.append('UNFINISHED_REPAIR_AT_TAIL');evidence.append(f'unmatchedQty={unmatched:.3f}, births={births}, completions={comps}')
        if fills<=1 or core<=EPS:
            causes.append('NO_EFFECTIVE_BASE_FORMATION');evidence.append(f'fills={fills}, coreFillQty={core:.3f}')
        if exp>core+sat+active and floor<0:
            causes.append('EXPAND_ECONOMICS_OR_EXPOSURE_QUALITY');evidence.append(f'expandQty={exp:.3f} dominates repair/core')
        if net>0 and pnl<0:
            causes.append('GOOD_PAIR_EDGE_BUT_WRONG_FINAL_EXPOSURE');evidence.append(f'netPairEdge={net:.3f} but pnl={pnl:.3f}')
        if flips>=2 and floor<0:
            causes.append('EXPOSURE_DIRECTION_OR_ASYMMETRY_LOSS');evidence.append(f'scopeFlips={flips}, floor={floor:.3f}')
        if submits>0 and fills/max(1,submits)<0.08 and pnl<0:
            causes.append('PASSIVE_EXECUTION_MISS_OR_QUEUE');evidence.append(f'fill/submit={fills/max(1,submits):.3f}')
    if not causes:
        causes=['NO_CLEAR_SINGLE_CAUSE'] if pnl<0 else ['WIN_OR_NONFAILURE']
    priority=['EXECUTION_OR_ACCOUNTING_ANOMALY','ACTIVE_REPAIR_PREMIUM_TOO_HIGH','OVER_REPAIR_OR_REPAIR_DENSITY','EXPENSIVE_PASSIVE_REPAIR','UNFINISHED_REPAIR_AT_TAIL','NO_EFFECTIVE_BASE_FORMATION','EXPAND_ECONOMICS_OR_EXPOSURE_QUALITY','GOOD_PAIR_EDGE_BUT_WRONG_FINAL_EXPOSURE','EXPOSURE_DIRECTION_OR_ASYMMETRY_LOSS','PASSIVE_EXECUTION_MISS_OR_QUEUE','NO_CLEAR_SINGLE_CAUSE','WIN_OR_NONFAILURE']
    causes=sorted(set(causes),key=lambda x:priority.index(x) if x in priority else 999)
    return causes[0],causes[1:],evidence

def main():
    allrows=[]
    for j in JOBS:
        p=ROOT/'data/research/lan_worker_returns'/j/'result.json'; d=json.loads(p.read_text(encoding='utf-8')); allrows.extend(d['rows'])
    outrows=[]; stats={}
    for cell in ['MS4_R28_CAP1_CONTROL','MS4_R213_ACTIVE_ECONOMIC_REMAINDER']:
        rr=[r for r in allrows if r['cell']==cell]; pn=[float(r['pnlDiagnosticOnly']) for r in rr]; fills=sum(int(r['fillEvents']) for r in rr); subs=sum(int(r['submits']) for r in rr)
        stats[cell]={'markets':len(rr),'wins':sum(x>0 for x in pn),'winRate':sum(x>0 for x in pn)/len(pn),'totalPnl':sum(pn),'meanPnl':sum(pn)/len(pn),'totalFloor':sum(float(r['floor']) for r in rr),'meanFloor':sum(float(r['floor']) for r in rr)/len(rr),'fills':fills,'submits':subs}
    cand=sorted([r for r in allrows if r['cell']=='MS4_R213_ACTIVE_ECONOMIC_REMAINDER'],key=lambda r:int(r['marketId']))
    counts=Counter()
    for r in cand:
        eco=fifo_pair(r); primary,secondary,evidence=attr(r,eco); counts[primary]+=1
        outrows.append({'marketId':int(r['marketId']),'winnerPostHocOnly':r.get('winnerPostHocOnly'),'pnl':float(r['pnlDiagnosticOnly']),'floor':float(r['floor']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'roleFillQty':r.get('roleFillQty',{}),'scopeBirths':int(r.get('scopeBirths') or 0),'scopeCompletions':int(r.get('scopeCompletions') or 0),'scopeFlips':int(r.get('scopeFlips') or 0),**eco,'primaryFailureCause':primary,'secondaryFailureCauses':secondary,'evidence':evidence,'economicRemainderSubmits':int(r.get('economicRemainderSubmits') or 0),'economicRemainderFilledKeys':int(r.get('economicRemainderFilledKeys') or 0),'economicRemainderFillQty':float(r.get('economicRemainderFillQty') or 0)})
    gate={'winRatePass':stats['MS4_R213_ACTIVE_ECONOMIC_REMAINDER']['winRate']>=0.5,'meanPnlPass':stats['MS4_R213_ACTIVE_ECONOMIC_REMAINDER']['meanPnl']>2.0,'graduationPass':stats['MS4_R213_ACTIVE_ECONOMIC_REMAINDER']['winRate']>=0.5 and stats['MS4_R213_ACTIVE_ECONOMIC_REMAINDER']['meanPnl']>2.0}
    out={'version':'MS4_R213_FULL24_CHARACTERIZATION_AND_FAILURE_ATTRIBUTION_V1','date':'2026-09-06','consumedCharacterizationOnly':True,'stats':stats,'graduationGateDiagnosticOnly':gate,'failureCauseCounts':dict(counts),'markets':outrows,'boundary':['consumed latest24; not fresh graduation evidence','failure attribution is deterministic post-hoc diagnostic from actual HFT fill/role/scope events','winner used only for post-hoc pnl scoring','no dream fill','no 8781']}
    op=ROOT/'data/research/r4_v0/p0_provenance_v1/MS4_R213_FULL24_CHARACTERIZATION_AND_FAILURE_ATTRIBUTION_V1_20260906.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'stats':stats,'gate':gate,'failureCauseCounts':dict(counts),'losses':[x for x in outrows if x['pnl']<=0]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
