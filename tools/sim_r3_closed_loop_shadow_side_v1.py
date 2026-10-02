import json, math, sqlite3, bisect, sys
from pathlib import Path
from collections import deque
import numpy as np, joblib
ROOT=Path(__file__).resolve().parents[1]; R=ROOT/'data/research/r3_v0'
M_SAFE=joblib.load(R/'r3_safe_expand_hgb_v3.joblib'); M_SIZE=joblib.load(R/'r3_safe_expand_size_hgb_v4.joblib'); M_BUD=joblib.load(R/'r3_safe_expand_floor_budget_hgb_v4.joblib'); M_SIDE=joblib.load(R/'r3_deliberate_side_hgb_v5b_public_ablation.joblib')
FILES=[ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b01_25.json',ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b02_26_50.json',ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b03_51_75.json',ROOT/'data/research/execution_aware_fill_lifecycle_v0/base_hist_b04_76_100.json']
PUBLIC=['predictUpMid','spotMinusStrikeBps','chainlinkMinusStrikeBps','spotMinusChainlinkBps','directionScore','spotReturn1sBps','spotReturn3sBps','spotReturn5sBps','futuresReturn1sBps','futuresReturn3sBps','futuresReturn5sBps','perpSpotBasisBps','spotQueueImbalance','futuresQueueImbalance','futuresTakerImbalance1s','secondsLeft']
def pred(b,f):
 x=np.array([[float(f.get(k,0.0) or 0.0) for k in b['features']]],float); m=b['model']; return float(m.predict_proba(x)[0,1]) if hasattr(m,'predict_proba') else float(m.predict(x)[0])
def reg(b,f):
 x=np.array([[float(f.get(k,0.0) or 0.0) for k in b['features']]],float); return max(0.0,float(b['model'].predict(x)[0]))
def load_pub(mids):
 c=sqlite3.connect(ROOT/'data/public_source_snapshot_archive_v2.db'); d={}
 q='select market_id,sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id between ? and ? order by market_id,sampled_at_ms'
 for mid,t,j in c.execute(q,(min(mids),max(mids))):
  if mid not in mids: continue
  d.setdefault(mid,[[],[]]); d[mid][0].append(int(t)); d[mid][1].append(json.loads(j))
 c.close(); return d
def pub_at(pub,mid,t):
 z=pub.get(mid)
 if not z:return None
 i=bisect.bisect_right(z[0],t)-1
 return None if i<0 or t-z[0][i]>3000 else z[1][i]
def feats(hist,up,down,cost,fees,i,n,t,role,side,px,sh):
 pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down; surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'; h15=[x for x in hist if t-x[0]<=15000]; h5=[x for x in h15 if t-x[0]<=5000]; old=h5[0] if h5 else h15[0]
 f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.0,'cost_per_gross_share':(cost+fees)/gross if gross else 0.0,'last_price':px,'last_shares':sh,'last_role_taker':1.0 if role=='TAKER' else 0.0,'age_since_last_ms':0.0 if len(hist)<2 else float(t-hist[-2][0]),'events_5s':float(len(h5)),'events_15s':float(len(h15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in h15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in h15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in h15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in h15)),'same_side_shares_15s':float(sum(x[3] for x in h15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in h15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':ss-old[4],'floor_change_5s':fl-old[5],'upside_change_5s':ups-old[6],'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.0,'floor_per_base_share':fl/base if base>1e-9 else 0.0,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.0,'event_index_norm':i/max(1,n-1)}
 return f,surplus,fl,ups
rows=[]
for fn in FILES: rows += json.load(open(fn,encoding='utf-8'))['rows']
start=int(sys.argv[1]) if len(sys.argv)>1 else 0; end=int(sys.argv[2]) if len(sys.argv)>2 else len(rows); rows=rows[start:end]; mids={int(r['marketId']) for r in rows}; pub=load_pub(mids)
def sim(row):
 fills=row.get('fillLog') or []
 if not fills:return None
 up=down=cost=fees=0.0; hist=deque(); last_expand=-10**18; ex=[]
 for i,e in enumerate(fills):
  t=int(e['eventMs']); role=str(e['role']); side=str(e['side']); px=float(e['price']); sh=float(e['shares']); fee=float(e.get('fee',0.0) or 0.0)
  if side=='UP':up+=sh
  else:down+=sh
  cost+=px*sh; fees+=fee; pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); hist.append((t,role,side,sh,ss,fl,ups))
  while hist and t-hist[0][0]>15000:hist.popleft()
  f,surplus,fl,ups=feats(hist,up,down,cost,fees,i,len(fills),t,role,side,px,sh)
  pctx=pub_at(pub,int(row['marketId']),t)
  if not pctx or fl<0 or t-last_expand<5000:continue
  for k in PUBLIC:f[k]=float(pctx.get(k) or 0.0)
  psafe=pred(M_SAFE,f)
  if psafe<0.5:continue
  pside=pred(M_SIDE,f); chosen='UP' if pside>=0.5 else 'DOWN'; desired=reg(M_SIZE,f); budget=reg(M_BUD,f)
  if desired<1e-6:continue
  # Use current observed side price proxy. If chosen side differs from current fill side, complement price is used as conservative public proxy.
  use_px=px if chosen==side else max(0.01,min(0.99,1.0-px))
  max_floor=fl/max(use_px,1e-9); max_budget=budget/max(use_px,1e-9); q=max(0.0,min(desired,max_floor,max_budget))
  if q<1.0:continue
  if chosen=='UP':up+=q
  else:down+=q
  cost+=use_px*q; ex.append({'t':t,'side':chosen,'price':use_px,'shares':q,'pSafe':psafe,'pSideUP':pside,'preFloor':fl}); last_expand=t
 win=str(row.get('winner') or ''); r3=(up if win=='UP' else down)-cost-fees; base=float(row.get('realizedPnl') or 0.0); floor=min(up-cost-fees,down-cost-fees); upside=max(up-cost-fees,down-cost-fees)
 return {'marketId':row['marketId'],'winner':win,'r2Pnl':base,'r3Pnl':r3,'delta':r3-base,'finalFloor':floor,'finalUpside':upside,'expansions':len(ex),'expansionNotional':sum(x['price']*x['shares'] for x in ex),'sampleExpansions':ex[:5]}
res=[sim(r) for r in rows]; res=[x for x in res if x]; arr=lambda k:np.array([x[k] for x in res],float); r2=arr('r2Pnl'); r3=arr('r3Pnl'); d=arr('delta')
s={'version':'R3_CLOSED_LOOP_SIDE_V1','range':[start,end],'markets':len(res),'r2Mean':float(r2.mean()),'r3Mean':float(r3.mean()),'meanDelta':float(d.mean()),'medianDelta':float(np.median(d)),'r2Median':float(np.median(r2)),'r3Median':float(np.median(r3)),'r2P10':float(np.quantile(r2,.1)),'r3P10':float(np.quantile(r3,.1)),'r2P90':float(np.quantile(r2,.9)),'r3P90':float(np.quantile(r3,.9)),'r2P95':float(np.quantile(r2,.95)),'r3P95':float(np.quantile(r3,.95)),'improvedMarkets':int((d>1e-9).sum()),'worsenedMarkets':int((d<-1e-9).sum()),'unchangedMarkets':int((abs(d)<=1e-9).sum()),'marketsWithExpansion':sum(x['expansions']>0 for x in res),'totalExpansions':sum(x['expansions'] for x in res)}
out=R/f'r3_closed_loop_side_{start:03d}_{end:03d}_v1.json'; out.write_text(json.dumps({'summary':s,'markets':res},indent=2),encoding='utf-8'); print(json.dumps(s,indent=2))
