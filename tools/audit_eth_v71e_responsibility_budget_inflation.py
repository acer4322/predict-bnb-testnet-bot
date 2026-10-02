from __future__ import annotations
import argparse,json,sqlite3,statistics,math,importlib.util,sys
from pathlib import Path
EPS=1e-9
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('v71base',Path(__file__).with_name('audit_eth_v71_generation_economic_anatomy.py'));v71=importlib.util.module_from_spec(spec);spec.loader.exec_module(v71)

def pct(xs,p):
 xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not xs:return None
 if len(xs)==1:return xs[0]
 z=(len(xs)-1)*p;i=int(z);f=z-i
 return xs[i]*(1-f)+xs[min(i+1,len(xs)-1)]*f

def responsibilities(components):
 by={}
 for x in components:by.setdefault(int(x['marketId']),[]).append(x)
 out=[]
 for mid,ev in by.items():
  ev=sorted(ev,key=lambda z:(int(z['t']),0 if z['kind']=='REPAIR' else 1,str(z.get('key',''))))
  cur=None
  for x in ev:
   if x['kind']!='EXPAND':continue
   t=int(x['t']);side=x['side'];q=float(x['qty']);notional=float(x.get('notional') or 0.0)
   if cur is None or side!=cur['side']:
    cur={'marketId':mid,'index':len([r for r in out if r['marketId']==mid])+1,'side':side,'bornAt':t,'lastExpandAt':t,'birthClockExpandQty':0.0,'birthClockNotional':0.0,'totalExpandQty':0.0,'totalNotional':0.0,'payments':0,'routes':set()};out.append(cur)
   if t==cur['bornAt']:
    cur['birthClockExpandQty']+=q;cur['birthClockNotional']+=notional
   cur['lastExpandAt']=max(cur['lastExpandAt'],t);cur['totalExpandQty']+=q;cur['totalNotional']+=notional;cur['payments']+=1;cur['routes'].add(str(x.get('route')))
  
 for r in out:
  b=max(r['birthClockExpandQty'],EPS);bn=max(r['birthClockNotional'],EPS)
  r['realizedBudgetMultiplier']=r['totalExpandQty']/b;r['notionalMultiplier']=r['totalNotional']/bn;r['extraBudgetQty']=max(0.0,r['totalExpandQty']-r['birthClockExpandQty']);r['durationSec']=(r['lastExpandAt']-r['bornAt'])/1000.;r['routes']=sorted(r['routes'])
 return out

def summarize(rs):
 mult=[r['realizedBudgetMultiplier'] for r in rs];dur=[r['durationSec'] for r in rs]
 return {'responsibilities':len(rs),'medianMultiplier':pct(mult,.5),'p75Multiplier':pct(mult,.75),'p90Multiplier':pct(mult,.9),'p95Multiplier':pct(mult,.95),'maxMultiplier':max(mult) if mult else None,'shareGt2x':sum(x>2 for x in mult)/len(mult) if mult else None,'shareGt3x':sum(x>3 for x in mult)/len(mult) if mult else None,'medianDurationSec':pct(dur,.5),'p90DurationSec':pct(dur,.9),'medianPayments':pct([r['payments'] for r in rs],.5),'p90Payments':pct([r['payments'] for r in rs],.9)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--target-db',default='data/target_wallet_official_v1.db');ap.add_argument('--v69',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json');ap.add_argument('--v70f',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V70F_EARLY_CLOCK_PARALLEL_RELAY_SMOKE_20260903.json');ap.add_argument('--output',required=True);a=ap.parse_args()
 v69=json.load(open(a.v69,encoding='utf-8'));end_by={}
 for row in v69['conditionRows']:
  mid=int(row['marketId'])
  if mid not in end_by:
   s=row['ourState'];end_by[mid]=int(round(float(s['snapshotT'])+float(s['remainingSec'])*1000.))
 con=sqlite3.connect(a.target_db);ph=','.join('?'*len(v71.STAGEA));rows=con.execute(f"select market_id,parent_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",v71.STAGEA).fetchall();con.close()
 tr=responsibilities(v71.target_components(rows,end_by,pre180=True));v70f=json.load(open(a.v70f,encoding='utf-8'));orr=responsibilities(v71.our_components(v70f));ts=summarize(tr);os=summarize(orr)
 our_current=max(orr,key=lambda r:r['totalExpandQty']) if orr else None;target_p90=ts['p90Multiplier'];our_mult=our_current['realizedBudgetMultiplier'] if our_current else None
 materially_above=bool(our_mult is not None and target_p90 is not None and our_mult>target_p90*1.5)
 decision='KEEP_PERSISTENT_SHARED_BUDGET_NO_RECURSIVE_BUDGET_MINTING' if materially_above else 'TARGET_SUPPORTS_COMPARABLE_BUDGET_REPLENISHMENT_ANALYZE_REPLENISHMENT_STATE'
 out={'version':'ETH_REPAIR_V71E_RESPONSIBILITY_BUDGET_INFLATION','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'target':ts,'ourV70F':os,'ourDominantResponsibility':our_current,'comparison':{'targetP90Multiplier':target_p90,'ourDominantMultiplier':our_mult,'ourVsTargetP90Ratio':our_mult/target_p90 if our_mult is not None and target_p90 not in (None,0) else None},'gate':{'v70fMateriallyAboveTargetP90':materially_above},'decision':decision,'targetResponsibilities':tr,'ourResponsibilities':orr,'boundary':['birth-clock grouping makes same-ms parent ordering irrelevant','descriptive only','2x/3x are reporting cutpoints not runtime thresholds','no controller change','no PnL/winner','no H100','no 8781']}
 Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'target':ts,'our':os,'comparison':out['comparison'],'ourDominantResponsibility':our_current},ensure_ascii=False))
if __name__=='__main__':main()
