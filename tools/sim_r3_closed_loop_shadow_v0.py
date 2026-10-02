import json, math
from pathlib import Path
from collections import deque
import numpy as np, joblib
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r3_v0'
M_SAFE=joblib.load(R/'r3_safe_expand_hgb_v3.joblib')
M_SIZE=joblib.load(R/'r3_safe_expand_size_hgb_v4.joblib')
M_BUD=joblib.load(R/'r3_safe_expand_floor_budget_hgb_v4.joblib')
FILES=[ROOT/f'data/research/execution_aware_fill_lifecycle_v0/base_hist_b0{i}_{a}.json' for i,a in [(1,'25'),(2,'26_50'),(3,'51_75'),(4,'76_100')]]
# exact filenames
FILES=[ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b01_25.json',ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b02_26_50.json',ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b03_51_75.json',ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b04_76_100.json']
OUT=R/'r3_closed_loop_shadow_100_v0.json'

def pred(bundle,feat):
    x=np.array([[float(feat.get(f,0.0)) for f in bundle['features']]],dtype=float)
    m=bundle['model']
    if hasattr(m,'predict_proba'): return float(m.predict_proba(x)[0,1])
    return float(m.predict(x)[0])

def reg(bundle,feat):
    x=np.array([[float(feat.get(f,0.0)) for f in bundle['features']]],dtype=float)
    return max(0.0,float(bundle['model'].predict(x)[0]))

def features(hist,up,down,cost,fees,i,n,t,role,side,px,sh):
    pu=up-cost-fees; pd=down-cost-fees; floor=min(pu,pd); upside=max(pu,pd); surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'; ss=abs(up-down); base=min(up,down); gross=up+down
    h15=[x for x in hist if t-x[0]<=15000]; h5=[x for x in h15 if t-x[0]<=5000]; old=h5[0] if h5 else h15[0]
    feat={'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.0,'cost_per_gross_share':(cost+fees)/gross if gross else 0.0,'last_price':px,'last_shares':sh,'last_role_taker':1.0 if role=='TAKER' else 0.0,'age_since_last_ms':0.0 if len(hist)<2 else float(t-hist[-2][0]),'events_5s':float(len(h5)),'events_15s':float(len(h15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in h15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in h15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in h15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in h15)),'same_side_shares_15s':float(sum(x[3] for x in h15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in h15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':ss-old[4],'floor_change_5s':floor-old[5],'upside_change_5s':upside-old[6],'floor_to_upside_ratio':floor/upside if abs(upside)>1e-9 else 0.0,'floor_per_base_share':floor/base if base>1e-9 else 0.0,'upside_per_surplus_share':upside/ss if ss>1e-9 else 0.0,'event_index_norm':i/max(1,n-1)}
    return feat,surplus,floor,upside

def sim(row):
    fills=row.get('fillLog') or []
    if not fills:return None
    up=down=cost=fees=0.0; hist=deque(); last_expand=-10**18; ex=[]
    for i,e in enumerate(fills):
        t=int(e['eventMs']); role=str(e['role']); side=str(e['side']); px=float(e['price']); sh=float(e['shares']); fee=float(e.get('fee',0.0) or 0.0)
        if side=='UP': up+=sh
        else: down+=sh
        cost+=px*sh; fees+=fee
        pu=up-cost-fees; pd=down-cost-fees; floor=min(pu,pd); upside=max(pu,pd); ss=abs(up-down)
        hist.append((t,role,side,sh,ss,floor,upside))
        while hist and t-hist[0][0]>15000: hist.popleft()
        feat,surplus,floor,upside=features(hist,up,down,cost,fees,i,len(fills),t,role,side,px,sh)
        if surplus=='FLAT' or floor<0 or t-last_expand<5000: continue
        p=pred(M_SAFE,feat)
        if p<0.5: continue
        desired=reg(M_SIZE,feat); budget=reg(M_BUD,feat)
        if desired<1e-6: continue
        # adding on surplus side at current observed fill price. Worst-outcome floor falls by px*q because losing side payout unchanged.
        max_by_floor=floor/max(px,1e-9)
        max_by_budget=budget/max(px,1e-9)
        q=max(0.0,min(desired,max_by_floor,max_by_budget))
        if q<1.0: continue
        if surplus=='UP': up+=q
        else: down+=q
        cost+=px*q
        ex.append({'t':t,'side':surplus,'price':px,'shares':q,'pSafe':p,'desired':desired,'floorBudget':budget,'preFloor':floor})
        last_expand=t
    win=str(row.get('winner') or '')
    r3=(up if win=='UP' else down)-cost-fees
    base=float(row.get('realizedPnl') or 0.0)
    floor=min(up-cost-fees,down-cost-fees); upside=max(up-cost-fees,down-cost-fees)
    return {'marketId':row['marketId'],'winner':win,'r2Pnl':base,'r3Pnl':r3,'delta':r3-base,'finalFloor':floor,'finalUpside':upside,'expansions':len(ex),'expansionNotional':sum(x['price']*x['shares'] for x in ex),'sampleExpansions':ex[:5]}
rows=[]
for f in FILES: rows.extend(json.load(open(f,encoding='utf-8'))['rows'])
import sys
start=int(sys.argv[1]) if len(sys.argv)>1 else 0
end=int(sys.argv[2]) if len(sys.argv)>2 else len(rows)
rows=rows[start:end]
res=[sim(r) for r in rows]; res=[x for x in res if x]
arr=lambda k: np.array([x[k] for x in res],float)
r2=arr('r2Pnl'); r3=arr('r3Pnl'); d=arr('delta')
summary={'version':'R3_CLOSED_LOOP_SHADOW_100_V0','markets':len(res),'r2Mean':float(r2.mean()),'r3Mean':float(r3.mean()),'meanDelta':float(d.mean()),'medianDelta':float(np.median(d)),'r2Median':float(np.median(r2)),'r3Median':float(np.median(r3)),'r2P10':float(np.quantile(r2,.1)),'r3P10':float(np.quantile(r3,.1)),'r2P90':float(np.quantile(r2,.9)),'r3P90':float(np.quantile(r3,.9)),'r2P95':float(np.quantile(r2,.95)),'r3P95':float(np.quantile(r3,.95)),'improvedMarkets':int((d>1e-9).sum()),'worsenedMarkets':int((d<-1e-9).sum()),'unchangedMarkets':int((abs(d)<=1e-9).sum()),'marketsWithExpansion':sum(x['expansions']>0 for x in res),'totalExpansions':sum(x['expansions'] for x in res),'avgExpansionNotional':float(np.mean([x['expansionNotional'] for x in res]))}
OUT=(R/f'r3_closed_loop_shadow_{start:03d}_{end:03d}_v0.json')
OUT.write_text(json.dumps({'summary':summary,'markets':res},indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
