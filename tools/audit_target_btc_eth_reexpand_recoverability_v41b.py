from __future__ import annotations
import sqlite3,json,math,bisect
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_ETH_REEXPAND_RECOVERABILITY_V41B.json'
EPS=1e-9

def pct(a,p):
 a=sorted(float(x) for x in a if x is not None and math.isfinite(float(x)))
 if not a:return None
 x=(len(a)-1)*p; lo=int(math.floor(x)); hi=int(math.ceil(x)); w=x-lo
 return a[lo] if lo==hi else a[lo]*(1-w)+a[hi]*w

def st(a):
 a=[float(x) for x in a if x is not None and math.isfinite(float(x))]
 return {'n':len(a),'mean':sum(a)/len(a) if a else None,'median':pct(a,.5),'p25':pct(a,.25),'p75':pct(a,.75),'p90':pct(a,.9)}

def progress_bin(x):
 if x<=EPS:return 'P0'
 if x<.25:return 'P0_25'
 if x<.5:return 'P25_50'
 if x<.75:return 'P50_75'
 return 'P75_100'

def summarize(rows):
 if not rows:return {'n':0}
 d={'n':len(rows),'markets':len(set(r['marketId'] for r in rows))}
 for h in (10,30):
  d[f'floorRecoverToPre{h}sRate']=sum(r[f'floor{h}']>=r['preFloor']-EPS for r in rows)/len(rows)
  d[f'floorNonnegative{h}sRate']=sum(r[f'floor{h}']>=-EPS for r in rows)/len(rows)
  d[f'floorDelta{h}s']=st([r[f'floor{h}']-r['postFloor'] for r in rows])
  d[f'bestDelta{h}s']=st([r[f'best{h}']-r['postBest'] for r in rows])
  d[f'repairShares{h}s']=st([r[f'repairShares{h}'] for r in rows])
  d[f'debtReductionFrac{h}s']=st([r[f'debtReductionFrac{h}'] for r in rows])
 d['preFloor']=st([r['preFloor'] for r in rows]); d['postFloor']=st([r['postFloor'] for r in rows]); d['preBest']=st([r['preBest'] for r in rows]); d['postBest']=st([r['postBest'] for r in rows]); d['repairProgressFrac']=st([r['repairProgressFrac'] for r in rows]); d['debtRemaining']=st([r['debtRemaining'] for r in rows]); d['expandNotional']=st([r['expandNotional'] for r in rows])
 return d

def main():
 con=sqlite3.connect(DB)
 evs=defaultdict(list)
 q='''select asset,market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where average_price is not null and shares>0 order by asset,market_id,first_event_ms,parent_id'''
 for a,m,role,side,t,p,qty,pid in con.execute(q):
  if str(role).upper()!='MAKER':continue
  evs[(str(a).upper(),int(m))].append((int(t),str(side).upper(),float(p),float(qty),pid))
 con.close()
 rows=[]
 for (asset,mid),xs in evs.items():
  U=D=C=0.0; debt=0.0; episode_initial=0.0; episode_repaired=0.0
  states=[]; cand=[]
  for i,(t,side,p,q,pid) in enumerate(xs):
   preU,preD,preC=U,D,C; preAbs=abs(U-D); preFloor=min(U,D)-C; preBest=max(U,D)-C; preDebt=debt
   if side=='UP':U+=q
   else:D+=q
   C+=p*q
   postAbs=abs(U-D); delta=postAbs-preAbs
   if preDebt<=EPS and delta>EPS:
    debt=delta; episode_initial=delta; episode_repaired=0.0; kind='EXPAND_NEW'
   elif preDebt>EPS and delta>EPS:
    prog=(episode_repaired/episode_initial) if episode_initial>EPS else 0.0
    debt+=delta; kind='REEXPAND'
   elif preDebt>EPS and delta< -EPS:
    pay=min(debt,-delta); debt-=pay; episode_repaired+=pay; kind='REPAIR'
    if debt<=EPS: debt=0.0
   else: kind='FLAT'
   postFloor=min(U,D)-C; postBest=max(U,D)-C
   states.append({'t':t,'floor':postFloor,'best':postBest,'debt':debt,'kind':kind,'abs':postAbs})
   if kind=='REEXPAND' and preDebt>EPS:
    cand.append({'idx':i,'t':t,'preFloor':preFloor,'postFloor':postFloor,'preBest':preBest,'postBest':postBest,'debtRemaining':preDebt,'repairProgressFrac':prog,'expandQty':q,'expandNotional':p*q})
  times=[s['t'] for s in states]
  for r in cand:
   baseDebt=r['debtRemaining']
   for h in (10,30):
    end=r['t']+h*1000; j=bisect.bisect_right(times,end)-1; j=max(r['idx'],j); s=states[j]
    repair=sum(max(0.0,states[k-1]['abs']-states[k]['abs']) for k in range(r['idx']+1,j+1)) if j>r['idx'] else 0.0
    r[f'floor{h}']=s['floor']; r[f'best{h}']=s['best']; r[f'repairShares{h}']=repair; r[f'debtReductionFrac{h}']=min(1.0,repair/baseDebt) if baseDebt>EPS else 0.0
   r['asset']=asset;r['marketId']=mid;rows.append(r)
 out={'version':'TARGET_BTC_ETH_REEXPAND_RECOVERABILITY_V41B','researchOnly':True,'actionAuthority':False,'definition':'Maker-only actual fills. Label future recoverability after re-expand while local expansion debt remains; future outcomes are research labels only, never runtime inputs.','coverage':{'rows':len(rows),'markets':len(set((r['asset'],r['marketId']) for r in rows))},'assets':{}}
 for a in ('BTC','ETH'):
  ar=[r for r in rows if r['asset']==a]
  out['assets'][a]={'ALL':summarize(ar),'byProgress':{b:summarize([r for r in ar if progress_bin(r['repairProgressFrac'])==b]) for b in ['P0','P0_25','P25_50','P50_75','P75_100']}}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
 print(json.dumps({'out':str(OUT.relative_to(ROOT)),'coverage':out['coverage'],'brief':{a:{'all':{k:v for k,v in out['assets'][a]['ALL'].items() if k in ['n','markets','floorRecoverToPre10sRate','floorRecoverToPre30sRate','floorNonnegative10sRate','floorNonnegative30sRate']},'byProgress':{b:{'n':z['n'],'recover10':z.get('floorRecoverToPre10sRate'),'recover30':z.get('floorRecoverToPre30sRate'),'debtRed30Med':z.get('debtReductionFrac30s',{}).get('median'),'bestDelta30Med':z.get('bestDelta30s',{}).get('median')} for b,z in out['assets'][a]['byProgress'].items()}} for a in ('BTC','ETH')}},indent=2))
if __name__=='__main__':main()
