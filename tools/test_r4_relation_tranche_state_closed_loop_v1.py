from __future__ import annotations
import json, math
from pathlib import Path
from statistics import median
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
FILES=[ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b01_25.json',ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b02_26_50.json',ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b03_51_75.json',ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b04_76_100.json']
OUT=ROOT/'data/research/r4_v0/hourly'
TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_RELATION_TRANCHE_STATE_CLOSED_LOOP_V1'
STRESSES=['NONE','WEAK_DROP_ALTERNATE','WEAK_PARTIAL_HALF_ALTERNATE','MAKER_DROP_EVERY5']

def geom(state):
    up,down,cu,cd=state; gross=up+down; paired=min(up,down); cost=cu+cd; floor=paired-cost
    au=cu/up if up>1e-12 else 0.0; ad=cd/down if down>1e-12 else 0.0
    edge=1.0-(au+ad) if up>1e-12 and down>1e-12 else 0.0
    return {'up':up,'down':down,'cost':cost,'gross':gross,'paired':paired,'floor':floor,'absnet':abs(up-down),'coverage':2*paired/gross if gross>1e-12 else 0.0,'edge':edge}

def apply(state,z,q):
    up,down,cu,cd=state; q=max(0.0,float(q)); px=float(z['px'])
    if z['side']=='UP': up+=q; cu+=px*q
    else: down+=q; cd+=px*q
    return (up,down,cu,cd)

def normalize(row):
    out=[]
    for e in sorted(row.get('fillLog') or [],key=lambda x:int(x['eventMs'])):
        sh=float(e.get('shares') or 0.0); px=float(e.get('price') or 0.0)
        if sh<=0 or not (0<=px<=1.05): continue
        out.append({'t':int(e['eventMs']),'role':str(e.get('role') or '').upper(),'side':str(e.get('side') or '').upper(),'px':px,'sh':sh})
    return [z for z in out if z['role'] in {'MAKER','TAKER'} and z['side'] in {'UP','DOWN'}]

def stress_stream(events,stress):
    state=(0.,0.,0.,0.); maker=weakctr=0; out=[]
    for z0 in events:
        z=dict(z0); pre=geom(state); q=z['sh']
        weak='UP' if pre['up']<pre['down'] else 'DOWN' if pre['down']<pre['up'] else None
        if z['role']=='MAKER':
            maker+=1
            if weak and z['side']==weak:
                weakctr+=1
                if stress=='WEAK_DROP_ALTERNATE' and weakctr%2==0: q=0.0
                elif stress=='WEAK_PARTIAL_HALF_ALTERNATE' and weakctr%2==0: q*=0.5
            if stress=='MAKER_DROP_EVERY5' and maker%5==0: q=0.0
        z['sh']=q; out.append(z); state=apply(state,z,q)
    return out

def safe_qty_zero_floor(state,z,full_q):
    full_q=max(0.0,float(full_q))
    if full_q<=0:return 0.0
    if geom(apply(state,z,full_q))['floor']>=0:return full_q
    lo,hi=0.0,full_q
    for _ in range(60):
        mid=(lo+hi)/2
        if geom(apply(state,z,mid))['floor']>=0:lo=mid
        else:hi=mid
    return lo

def replay(events,winner,overlay=False):
    state=(0.,0.,0.,0.); modified=flags=tranches=crosshard=0; suppressed=0.0; first_pos=False; reserve_spent=0.0
    floors=[]; times=[]; peak=-1e99; maxdd=0.0
    for z in events:
        pre=geom(state); q=float(z['sh']); postfull=geom(apply(state,z,q)); reserve=max(0.0,pre['floor']-postfull['floor'])
        flag=q>0 and pre['floor']>0 and reserve>1e-12 and postfull['edge']<0
        if overlay and flag:
            flags+=1
            surplus='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
            relation='SURPLUS_SIDE' if z['side']==surplus else 'WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT'
            q0=q
            if relation=='SURPLUS_SIDE':
                q=safe_qty_zero_floor(state,z,q)
                if q<q0-1e-9:tranches+=1
            else:
                q=0.0
                if relation=='WEAK_SIDE_CROSS':crosshard+=1
            if q<q0-1e-9:
                modified+=1; suppressed+=q0-q
        state=apply(state,z,q); cur=geom(state)
        if first_pos and cur['floor']<pre['floor']: reserve_spent+=pre['floor']-cur['floor']
        if cur['floor']>0:first_pos=True
        peak=max(peak,cur['floor']); maxdd=max(maxdd,peak-cur['floor'])
        floors.append(cur['floor']); times.append(int(z['t']))
    g=geom(state); duration=0.0
    for i in range(len(floors)-1):
        if floors[i]>0: duration+=(times[i+1]-times[i])/1000.0
    payout=g['up'] if winner=='UP' else g['down'] if winner=='DOWN' else 0.0
    return {**g,'pnl':payout-g['cost'],'positiveDurationSec':duration,'maxFloorDrawdown':maxdd,'reserveSpentAfterBase':reserve_spent,'modifiedEvents':modified,'flags':flags,'tranches':tranches,'crossHard':crosshard,'suppressedShares':suppressed}

def md(xs): return float(median(xs)) if xs else None

def q90(xs): return float(np.quantile(np.asarray(xs,float),.9)) if xs else None

def summarize(rows,stress):
    rec=[]
    for r in rows:
        ev=normalize(r)
        if not ev or str(r.get('winner')) not in {'UP','DOWN'}:continue
        sev=stress_stream(ev,stress)
        b=replay(sev,str(r['winner']),False); o=replay(sev,str(r['winner']),True)
        if o['modifiedEvents']<=0:continue
        rec.append((int(r['marketId']),b,o))
    def delta(k): return [o[k]-b[k] for _,b,o in rec]
    active=len(rec)
    out={'activeMarkets':active,'modifiedEvents':sum(o['modifiedEvents'] for _,_,o in rec),'trancheEvents':sum(o['tranches'] for _,_,o in rec),'crossHardEvents':sum(o['crossHard'] for _,_,o in rec),
         'medianDeltaFinalFloor':md(delta('floor')),'medianDeltaPositiveDurationSec':md(delta('positiveDurationSec')),
         'medianDeltaMaxFloorDrawdown':md(delta('maxFloorDrawdown')),'medianDeltaReserveSpentAfterBase':md(delta('reserveSpentAfterBase')),
         'medianDeltaCoverage':md(delta('coverage')),'medianDeltaAbsNet':md(delta('absnet')),'medianDeltaPairEdge':md(delta('edge')),'medianDeltaPnl':md(delta('pnl')),
         'baselineAbsNetP90':q90([b['absnet'] for _,b,_ in rec]),'overlayAbsNetP90':q90([o['absnet'] for _,_,o in rec]),
         'floorImproved':sum(o['floor']>b['floor']+1e-9 for _,b,o in rec),'floorWorsened':sum(o['floor']<b['floor']-1e-9 for _,b,o in rec)}
    tail_ok=(out['overlayAbsNetP90'] is None or out['baselineAbsNetP90'] is None or out['overlayAbsNetP90']<=out['baselineAbsNetP90']*1.10+1e-9)
    out['pass']=bool(active>=5 and out['medianDeltaFinalFloor'] is not None and out['medianDeltaFinalFloor']>=0 and out['medianDeltaMaxFloorDrawdown'] is not None and out['medianDeltaMaxFloorDrawdown']<=1e-9 and out['medianDeltaReserveSpentAfterBase'] is not None and out['medianDeltaReserveSpentAfterBase']<=1e-9 and tail_ok)
    return out

def main():
    rows=[]
    for f in FILES: rows.extend(json.loads(f.read_text(encoding='utf-8'))['rows'])
    rows=[r for r in rows if normalize(r)]
    rows.sort(key=lambda r:min(z['t'] for z in normalize(r)))
    # No fitting: recent 40 is Level-S; expand to all available 100 only if Level-S normal is promising.
    level_s_rows=rows[-40:]
    level_s={s:summarize(level_s_rows,s) for s in STRESSES}
    stress_pass=sum(level_s[s]['pass'] for s in STRESSES[1:] if level_s[s]['activeMarkets']>=5)
    s_promising=bool(level_s['NONE']['pass'] and stress_pass>=2)
    level_m=None; keep=False
    if s_promising:
        level_m={s:summarize(rows,s) for s in STRESSES}
        eligible=[s for s in STRESSES[1:] if level_m[s]['activeMarkets']>=10]
        keep=bool(level_m['NONE']['activeMarkets']>=10 and level_m['NONE']['pass'] and len(eligible)>=2 and sum(level_m[s]['pass'] for s in eligible)>=2)
    status='KEEP_SIGNAL' if keep else 'REJECTED' if not s_promising else 'INCONCLUSIVE'
    now=datetime.now(TZ)
    report={'version':VERSION,'createdAt':now.isoformat(),'candidate':'State-closed-loop validation of the existing relation-aware reserve-capped tranche on non-live confirmed PAPER/HFT fill streams. Requested action stream is frozen, but every later MPQ flag/relation/tranche decision is recomputed from the inventory produced by prior overlay decisions. WEAK_SIDE_CROSS/FLAT hard-protected; SURPLUS_SIDE capped at exact floor=0 boundary.',
            'source':{'files':[str(f.relative_to(ROOT)).replace('\\','/') for f in FILES],'markets':len(rows),'levelSRecentMarkets':len(level_s_rows),'nonLiveOnly':True},'levelS':level_s,'levelSPromising':s_promising,'levelM':level_m,'status':status,
            'gate':{'levelS':'normal pass + >=2 stress variants pass; each requires >=5 active markets, median final floor >=0, median max-floor-drawdown delta <=0, median reserve-spent-after-base delta <=0, abs-net P90 <=110% baseline','levelM':'normal >=10 active and pass + >=2 eligible stress variants (>=10 active) pass same rule'},
            'guards':{'strictPastState':True,'winnerEvaluationOnly':True,'futureTargetRuntimeInput':False,'special20260816Sealed':True,'noEchtgeldTraining':True,'frozenLiveOnlyStressSemantics':True,'noThresholdSweep':True,'floorBoundary':0.0,'noLiveR3Change':True,'no8781Change':True,'researchOnly':True},
            'limitation':'This is state-closed-loop overlay validation, not a fully regenerated policy action stream: future requested action side/price/share requests remain from the recorded non-live stream, while overlay eligibility/relation/sizing responds causally to modified inventory.'}
    out=OUT/f"r4_relation_tranche_state_closed_loop_v1_{now.strftime('%Y%m%d_%H%M%S')}.json";out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'levelS':level_s,'levelSPromising':s_promising,'levelM':level_m,'status':status},ensure_ascii=False))
if __name__=='__main__':main()
