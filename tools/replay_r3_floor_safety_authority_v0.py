from __future__ import annotations
import json, sqlite3
from pathlib import Path
from collections import deque, Counter
import numpy as np, joblib

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'r3_v0'/'r3_floor_safety_authority_v0.json'
BUNDLES=[joblib.load(ROOT/'data'/'research'/'r3_v0'/n) for n in ['r3_surplus_permission_hgb_v1.joblib','r3_surplus_expansion_hgb_v1.joblib','r3_floor_protection_hgb_v0.joblib']]
CHILD=18.0

def fee(sh,px,role): return sh*px*.02 if role=='TAKER' else 0.0

def probs(bundle, feats):
    X=np.asarray([[float(f.get(k,0.0)) for k in bundle['features']] for f in feats],dtype=float)
    return bundle['model'].predict_proba(X)[:,1]

def build(c,mid):
    rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
    if len(rr)<4:return []
    up=down=cost=fees=0.; hist=deque(); prev=None; out=[]
    for i,(role,side,t,px,sh) in enumerate(rr[:-1]):
        role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
        if side=='UP':up+=sh
        else:down+=sh
        cost+=px*sh; fees+=fee(sh,px,role)
        pu=up-cost-fees; pd=down-cost-fees; floor=min(pu,pd); upside=max(pu,pd)
        surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'; ss=abs(up-down); base=min(up,down); gross=up+down
        hist.append((t,role,side,sh,ss,floor,upside))
        while hist and t-hist[0][0]>15000:hist.popleft()
        r5=[x for x in hist if t-x[0]<=5000]; old=r5[0] if r5 else hist[0]; r15=list(hist)
        feat={'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev is None else float(t-prev),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':ss-old[4],'floor_change_5s':floor-old[5],'upside_change_5s':upside-old[6],'floor_to_upside_ratio':floor/upside if abs(upside)>1e-9 else 0.,'floor_per_base_share':floor/base if base>1e-9 else 0.,'upside_per_surplus_share':upside/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1)}
        if surplus!='FLAT' and ss>=5:
            nxt=str(rr[i+1][1]); actual='EXPAND' if nxt==surplus else 'REPAIR'
            # diagnostic one-child maker expansion at current parent price; strict-past price proxy only
            projected_floor=floor-CHILD*px
            projected_upside=upside+CHILD*(1-px)
            out.append({'feat':feat,'actual':actual,'floor':floor,'upside':upside,'px':px,'projectedFloor':projected_floor,'projectedUpside':projected_upside})
        prev=t
    return out

def main():
    c=sqlite3.connect(DB)
    mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]
    test=mids[int(.8*len(mids)):]
    rows=[]
    for m in test: rows.extend(build(c,m))
    c.close()
    feats=[r['feat'] for r in rows]; pp=probs(BUNDLES[0],feats); pe=probs(BUNDLES[1],feats); ps=probs(BUNDLES[2],feats)
    raw=[]
    for i,r in enumerate(rows):
        act='PROTECT' if ps[i]>=.5 else 'REPAIR' if pp[i]<.5 else 'EXPAND' if pe[i]>=.5 else 'HOLD'
        raw.append((act,r))
    exp=[r for a,r in raw if a=='EXPAND']
    gates={}
    for name,fn in {
      'CURRENT_FLOOR_GE_0':lambda r:r['floor']>=0,
      'PROJECTED_FLOOR_GE_0':lambda r:r['projectedFloor']>=0,
      'PROJECTED_FLOOR_GE_5':lambda r:r['projectedFloor']>=5,
      'PROJECTED_FLOOR_GE_10':lambda r:r['projectedFloor']>=10,
    }.items():
        keep=[r for r in exp if fn(r)]
        gates[name]={'kept':len(keep),'keepRate':len(keep)/len(exp) if exp else None,'nextSideExpandPrecision':sum(r['actual']=='EXPAND' for r in keep)/len(keep) if keep else None,'medianCurrentFloor':float(np.median([r['floor'] for r in keep])) if keep else None,'medianCurrentUpside':float(np.median([r['upside'] for r in keep])) if keep else None,'medianProjectedFloor':float(np.median([r['projectedFloor'] for r in keep])) if keep else None,'medianProjectedUpside':float(np.median([r['projectedUpside'] for r in keep])) if keep else None}
    rep={'version':'R3_FLOOR_SAFETY_AUTHORITY_V0','boundary':'final-20% chronological Target shadow; winner excluded','diagnosticAssumption':'Projected one-child Maker expansion uses 18 shares at current parent average price as a strict-past price proxy. It is a safety audit, not a fill claim.','checkpoints':len(rows),'rawExpand':len(exp),'rawExpandPrecision':sum(r['actual']=='EXPAND' for r in exp)/len(exp),'gates':gates}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
