from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path
from collections import deque,Counter
import numpy as np, joblib
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUTDIR=ROOT/'data'/'research'/'r3_v0'/'shadow_batches_v0'; OUTDIR.mkdir(parents=True,exist_ok=True)
M1=joblib.load(ROOT/'data'/'research'/'r3_v0'/'r3_surplus_permission_hgb_v1.joblib')
M2=joblib.load(ROOT/'data'/'research'/'r3_v0'/'r3_surplus_expansion_hgb_v1.joblib')
M3=joblib.load(ROOT/'data'/'research'/'r3_v0'/'r3_floor_protection_hgb_v0.joblib')
def fee(sh,px,role): return sh*px*0.02 if role=='TAKER' else 0.0
def build_rows(c,mids):
    feats=[]; meta=[]
    for mid in mids:
        rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
        if len(rr)<4: continue
        up=down=cost=fees=0.0; hist=deque(); prev_t=None
        for i,(role,side,t,px,sh) in enumerate(rr[:-1]):
            role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
            if side=='UP': up+=sh
            else: down+=sh
            cost+=px*sh; fees+=fee(sh,px,role)
            pu=up-cost-fees; pd=down-cost-fees; floor=min(pu,pd); upside=max(pu,pd); gap=upside-floor
            surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'; surplus_sh=abs(up-down); base=min(up,down); gross=up+down
            hist.append((t,role,side,sh,surplus_sh,floor,upside))
            while hist and t-hist[0][0]>15000: hist.popleft()
            r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old5=r5[0] if r5 else hist[0]
            if surplus!='FLAT' and surplus_sh>=5:
                feat={'floor':floor,'upside':upside,'upside_gap':gap,'surplus_shares':surplus_sh,'base_pair_shares':base,'surplus_ratio':surplus_sh/gross if gross else 0.0,'cost_per_gross_share':(cost+fees)/gross if gross else 0.0,'last_price':px,'last_shares':sh,'last_role_taker':1.0 if role=='TAKER' else 0.0,'age_since_last_ms':0.0 if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if x[2]!=surplus)),'surplus_change_5s':float(surplus_sh-old5[4]),'floor_change_5s':float(floor-old5[5]),'upside_change_5s':float(upside-old5[6]),'floor_to_upside_ratio':floor/upside if abs(upside)>1e-9 else 0.0,'floor_per_base_share':floor/base if base>1e-9 else 0.0,'upside_per_surplus_share':upside/surplus_sh if surplus_sh>1e-9 else 0.0,'event_index_norm':i/max(1,len(rr)-1)}
                feats.append(feat); meta.append((mid,t,str(rr[i+1][1])==surplus,floor,upside))
            prev_t=t
    return feats,meta
def mat(feats,fs): return np.asarray([[float(f.get(k,0.0)) for k in fs] for f in feats],dtype=float)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--start',type=int,required=True); ap.add_argument('--end',type=int,required=True); a=ap.parse_args()
    c=sqlite3.connect(DB); allm=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; test=allm[int(.8*len(allm)):]; mids=test[a.start:a.end]
    feats,meta=build_rows(c,mids); c.close()
    if not feats:
        rep={'start':a.start,'end':a.end,'markets':len(mids),'checkpoints':0}; print(json.dumps(rep)); return
    p1=M1['model'].predict_proba(mat(feats,M1['features']))[:,1]; p2=M2['model'].predict_proba(mat(feats,M2['features']))[:,1]; p3=M3['model'].predict_proba(mat(feats,M3['features']))[:,1]
    acts=[]
    for (mid,t,actual_expand,floor,upside),pp,pe,ps in zip(meta,p1,p2,p3):
        act='PROTECT' if ps>=.5 else 'REPAIR' if pp<.5 else 'EXPAND' if pe>=.5 else 'HOLD'; acts.append((act,'EXPAND' if actual_expand else 'REPAIR',floor,upside))
    cnt=Counter(x[0] for x in acts); actual=Counter(x[1] for x in acts)
    def prec(name,label):
        xs=[x for x in acts if x[0]==name]; return sum(x[1]==label for x in xs)/len(xs) if xs else None
    ex=[x for x in acts if x[0]=='EXPAND']
    rep={'version':'R3_HIERARCHICAL_SHADOW_BATCH_FAST_V0','start':a.start,'end':a.end,'requestedMarkets':len(mids),'checkpoints':len(acts),'actionCounts':dict(cnt),'actualNextCounts':dict(actual),'metrics':{'expandPrecisionVsNextSide':prec('EXPAND','EXPAND'),'holdPermissionPrecisionVsNextSide':prec('HOLD','EXPAND'),'protectPrecisionVsNextSideRepair':prec('PROTECT','REPAIR'),'repairPrecisionVsNextSideRepair':prec('REPAIR','REPAIR'),'expandFloorGe0Rate':sum(x[2]>=0 for x in ex)/len(ex) if ex else None,'expandMedianFloor':float(np.median([x[2] for x in ex])) if ex else None,'expandMedianUpside':float(np.median([x[3] for x in ex])) if ex else None}}
    out=OUTDIR/f'fast_{a.start:04d}_{a.end:04d}.json'; out.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps({'out':str(out),'summary':rep},indent=2))
if __name__=='__main__': main()
