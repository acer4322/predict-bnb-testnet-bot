from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from collections import deque, Counter
import numpy as np, joblib

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'r3_v0'/'r3_hierarchical_shadow_replay_v0.json'
M1=joblib.load(ROOT/'data'/'research'/'r3_v0'/'r3_surplus_permission_hgb_v1.joblib')
M2=joblib.load(ROOT/'data'/'research'/'r3_v0'/'r3_surplus_expansion_hgb_v1.joblib')
M3=joblib.load(ROOT/'data'/'research'/'r3_v0'/'r3_floor_protection_hgb_v0.joblib')

# common strict-past features used by v1 experts
F1=M1['features']; F2=M2['features']; F3=M3['features']

def fee(sh,px,role): return sh*px*0.02 if role=='TAKER' else 0.0

def vec(feat, fs): return np.asarray([[float(feat.get(f,0.0)) for f in fs]],dtype=float)
def prob(bundle, feat): return float(bundle['model'].predict_proba(vec(feat,bundle['features']))[0,1])

def market_rows(c,mid):
    return c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()

def replay_market(c,mid):
    rr=market_rows(c,mid)
    if len(rr)<4:return None
    up=down=cost=fees=0.0; hist=deque(); prev_t=None
    actions=[]; safe_expand=0; target_expand=0; correct_expand=0; protect_hits=0; permission_hits=0
    for i,(role,side,t,px,sh) in enumerate(rr[:-1]):
        role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
        if side=='UP': up+=sh
        else: down+=sh
        cost+=px*sh; fees+=fee(sh,px,role)
        pnl_up=up-cost-fees; pnl_down=down-cost-fees
        floor=min(pnl_up,pnl_down); upside=max(pnl_up,pnl_down); gap=upside-floor
        if up>down: surplus='UP'
        elif down>up: surplus='DOWN'
        else: surplus='FLAT'
        surplus_sh=abs(up-down); base=min(up,down); gross=up+down
        hist.append((t,role,side,sh,surplus_sh,floor,upside))
        while hist and t-hist[0][0]>15000:hist.popleft()
        r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist)
        old5=r5[0] if r5 else hist[0]
        feat={
          'floor':floor,'upside':upside,'upside_gap':gap,'surplus_shares':surplus_sh,'base_pair_shares':base,
          'surplus_ratio':surplus_sh/gross if gross else 0.0,'cost_per_gross_share':(cost+fees)/gross if gross else 0.0,
          'last_price':px,'last_shares':sh,'last_role_taker':1.0 if role=='TAKER' else 0.0,
          'age_since_last_ms':0.0 if prev_t is None else float(t-prev_t),
          'events_5s':float(len(r5)),'events_15s':float(len(r15)),
          'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),
          'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),
          'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),
          'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),
          'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),
          'surplus_change_5s':float(surplus_sh-old5[4]),'floor_change_5s':float(floor-old5[5]),'upside_change_5s':float(upside-old5[6]),
          'floor_to_upside_ratio':floor/upside if abs(upside)>1e-9 else 0.0,
          'floor_per_base_share':floor/base if base>1e-9 else 0.0,
          'upside_per_surplus_share':upside/surplus_sh if surplus_sh>1e-9 else 0.0,
          'event_index_norm':i/max(1,len(rr)-1)
        }
        if surplus=='FLAT' or surplus_sh<5:
            prev_t=t; continue
        pp=prob(M1,feat); pe=prob(M2,feat); ps=prob(M3,feat)
        if ps>=0.5: act='PROTECT'
        elif pp<0.5: act='REPAIR'
        elif pe>=0.5: act='EXPAND'
        else: act='HOLD'
        next_side=str(rr[i+1][1]); actual='EXPAND' if next_side==surplus else 'REPAIR'
        if act in ('HOLD','EXPAND'): permission_hits += int(actual=='EXPAND')
        if act=='EXPAND':
            target_expand+=1; correct_expand+=int(actual=='EXPAND'); safe_expand+=int(floor>=0)
        if act=='PROTECT': protect_hits+=int(actual=='REPAIR')
        actions.append({'t':t,'action':act,'actualNext':actual,'pPermission':pp,'pExpansion':pe,'pProtect':ps,'floor':floor,'upside':upside,'surplus':surplus_sh})
        prev_t=t
    if not actions:return None
    cnt=Counter(a['action'] for a in actions)
    return {'marketId':mid,'n':len(actions),'counts':dict(cnt),'expandPrecision':correct_expand/target_expand if target_expand else None,'expandSafeFloorRate':safe_expand/target_expand if target_expand else None,'protectRepairPrecision':protect_hits/cnt.get('PROTECT',0) if cnt.get('PROTECT',0) else None,'actions':actions}

def main():
    c=sqlite3.connect(DB)
    mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]
    # use final 20% chronological markets only for shadow replay evaluation
    test=mids[int(.8*len(mids)):]
    out=[]
    for m in test:
        z=replay_market(c,m)
        if z: out.append(z)
    c.close()
    allacts=[a for m in out for a in m['actions']]
    cnt=Counter(a['action'] for a in allacts)
    actual=Counter(a['actualNext'] for a in allacts)
    expand=[a for a in allacts if a['action']=='EXPAND']; protect=[a for a in allacts if a['action']=='PROTECT']; hold=[a for a in allacts if a['action']=='HOLD']; repair=[a for a in allacts if a['action']=='REPAIR']
    def prec(xs,label): return sum(a['actualNext']==label for a in xs)/len(xs) if xs else None
    rep={'version':'R3_HIERARCHICAL_SHADOW_REPLAY_V0','boundary':'Target chronological final-20% shadow replay only. Experts observe strict-past reconstructed state; actions do not alter Target/R2 fills. Winner not used.',
         'markets':len(out),'checkpoints':len(allacts),'actionCounts':dict(cnt),'actualNextCounts':dict(actual),
         'metrics':{'expandPrecisionVsNextSide':prec(expand,'EXPAND'),'holdPermissionPrecisionVsNextSide':prec(hold,'EXPAND'),'protectPrecisionVsNextSideRepair':prec(protect,'REPAIR'),'repairPrecisionVsNextSideRepair':prec(repair,'REPAIR'),
                    'expandFloorGe0Rate':sum(a['floor']>=0 for a in expand)/len(expand) if expand else None,'expandMedianFloor':float(np.median([a['floor'] for a in expand])) if expand else None,'expandMedianUpside':float(np.median([a['upside'] for a in expand])) if expand else None},
         'sampleMarkets':[{k:v for k,v in m.items() if k!='actions'} for m in out[:30]]}
    OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(rep,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
