from __future__ import annotations
import sys,json,sqlite3
from pathlib import Path
from collections import deque
import numpy as np, joblib
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data'/'target_wallet_official_v1.db'; OUTDIR=ROOT/'data'/'research'/'r3_v0'/'floor_safety_batches_v0'; OUTDIR.mkdir(parents=True,exist_ok=True)
M1=joblib.load(ROOT/'data'/'research'/'r3_v0'/'r3_surplus_permission_hgb_v1.joblib'); M2=joblib.load(ROOT/'data'/'research'/'r3_v0'/'r3_surplus_expansion_hgb_v1.joblib'); M3=joblib.load(ROOT/'data'/'research'/'r3_v0'/'r3_floor_protection_hgb_v0.joblib'); CHILD=18.0
def fee(sh,p,r):return sh*p*.02 if r=='TAKER' else 0.
def build(c,mid):
 rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall();
 if len(rr)<4:return []
 up=down=cost=fees=0.; hist=deque(); prev=None; out=[]
 for i,(role,side,t,px,sh) in enumerate(rr[:-1]):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh);up+=sh if side=='UP' else 0;down+=sh if side=='DOWN' else 0;cost+=px*sh;fees+=fee(sh,px,role);pu=up-cost-fees;pd=down-cost-fees;floor=min(pu,pd);upside=max(pu,pd);sur='UP' if up>down else 'DOWN' if down>up else 'FLAT';ss=abs(up-down);base=min(up,down);gross=up+down;hist.append((t,role,side,sh,ss,floor,upside));
  while hist and t-hist[0][0]>15000:hist.popleft()
  r5=[x for x in hist if t-x[0]<=5000];old=r5[0] if r5 else hist[0];r15=list(hist)
  feat={'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev is None else float(t-prev),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(sur!='FLAT' and x[2]==sur for x in r15)),'opp_side_events_15s':float(sum(sur!='FLAT' and x[2]!=sur for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur)),'surplus_change_5s':ss-old[4],'floor_change_5s':floor-old[5],'upside_change_5s':upside-old[6],'floor_to_upside_ratio':floor/upside if abs(upside)>1e-9 else 0.,'floor_per_base_share':floor/base if base>1e-9 else 0.,'upside_per_surplus_share':upside/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1)}
  if sur!='FLAT' and ss>=5:out.append({'feat':feat,'actual':'EXPAND' if str(rr[i+1][1])==sur else 'REPAIR','floor':floor,'upside':upside,'projectedFloor':floor-CHILD*px,'projectedUpside':upside+CHILD*(1-px)})
  prev=t
 return out
def pv(m,fs):return m['model'].predict_proba(np.asarray([[float(f.get(k,0.)) for k in m['features']] for f in fs],dtype=float))[:,1]
def main():
 s,e=map(int,sys.argv[1:3]);c=sqlite3.connect(DB);mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")];test=mids[int(.8*len(mids)):];rows=[]
 for mid in test[s:e]:rows.extend(build(c,mid))
 c.close();fs=[r['feat'] for r in rows];p1=pv(M1,fs);p2=pv(M2,fs);p3=pv(M3,fs);exp=[r for i,r in enumerate(rows) if p3[i]<.5 and p1[i]>=.5 and p2[i]>=.5];rules={'CURRENT_FLOOR_GE_0':lambda r:r['floor']>=0,'PROJECTED_FLOOR_GE_0':lambda r:r['projectedFloor']>=0,'PROJECTED_FLOOR_GE_5':lambda r:r['projectedFloor']>=5,'PROJECTED_FLOOR_GE_10':lambda r:r['projectedFloor']>=10};g={}
 for n,fn in rules.items():
  k=[r for r in exp if fn(r)];g[n]={'kept':len(k),'correct':sum(r['actual']=='EXPAND' for r in k),'floors':[r['floor'] for r in k],'upsides':[r['upside'] for r in k],'pf':[r['projectedFloor'] for r in k],'pu':[r['projectedUpside'] for r in k]}
 out={'s':s,'e':e,'markets':len(test[s:e]),'checkpoints':len(rows),'rawExpand':len(exp),'rawCorrect':sum(r['actual']=='EXPAND' for r in exp),'gates':g};p=OUTDIR/f'fast_{s:04d}_{e:04d}.json';p.write_text(json.dumps(out),encoding='utf-8');print(json.dumps({'out':str(p),'rawExpand':len(exp),'gates':{n:{'kept':v['kept'],'precision':v['correct']/v['kept'] if v['kept'] else None} for n,v in g.items()}},indent=2))
if __name__=='__main__':main()
