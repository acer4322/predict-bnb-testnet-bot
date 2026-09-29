from __future__ import annotations
import json,sqlite3,statistics,math,importlib.util,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9
spec=importlib.util.spec_from_file_location('v71',Path(__file__).with_name('audit_eth_v71_generation_economic_anatomy.py'));v71=importlib.util.module_from_spec(spec);spec.loader.exec_module(v71)

def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return statistics.median(xs) if xs else None

def build_components(db,mids,end_by=None,pre180=False):
 con=sqlite3.connect(db);ph=','.join('?'*len(mids));rows=con.execute(f"select market_id,parent_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",mids).fetchall();con.close()
 if pre180:return v71.target_components(rows,end_by or {},pre180=True)
 # No time clipping; fake far end so target_components keeps all.
 far={int(m):10**18 for m in mids};return v71.target_components(rows,far,pre180=True)

def audit(comps):
 by={}
 for x in comps:by.setdefault(int(x['marketId']),[]).append(x)
 rows=[]
 for mid,ev in by.items():
  ev=sorted(ev,key=lambda z:(int(z['t']),str(z.get('parentId')),int(z.get('componentIndex',0))))
  parents={}
  for x in ev:
   k=(int(x['t']),str(x.get('parentId')));parents.setdefault(k,[]).append(x)
  plist=sorted(parents.items())
  for i,((t,pid),parts) in enumerate(plist):
   kinds={x['kind'] for x in parts}
   if not {'REPAIR','EXPAND'}.issubset(kinds):continue
   rep=sum(float(x['qty']) for x in parts if x['kind']=='REPAIR');exp=sum(float(x['qty']) for x in parts if x['kind']=='EXPAND');side=str(parts[0]['side']);route=str(parts[0]['route']);opp='DOWN' if side=='UP' else 'UP';nxt=None
   for (tt,pp),pparts in plist[i+1:]:
    if int(tt)<=t:continue
    rr=[x for x in pparts if x['kind']=='REPAIR' and str(x['side'])==opp]
    if rr:
     nxt={'t':int(tt),'lagSec':(int(tt)-t)/1000.0,'route':str(rr[0]['route']),'side':opp,'repairQty':sum(float(x['qty']) for x in rr),'parentId':str(pp),'isComposite':any(x['kind']=='EXPAND' for x in pparts)};break
   rows.append({'marketId':mid,'t':t,'side':side,'route':route,'repairAllocation':rep,'overflowAllocation':exp,'nextOppositeRepair':nxt})
 n=len(rows);withn=[r for r in rows if r['nextOppositeRepair']];within30=[r for r in withn if r['nextOppositeRepair']['lagSec']<=30];routes={}
 for r in withn:
  rt=r['nextOppositeRepair']['route'];z=routes.setdefault(rt,{'n':0,'within30':0,'coversOverflow':0,'lags':[],'coverage':[]});z['n']+=1;z['within30']+=r['nextOppositeRepair']['lagSec']<=30;z['coversOverflow']+=r['nextOppositeRepair']['repairQty']+EPS>=r['overflowAllocation'];z['lags'].append(r['nextOppositeRepair']['lagSec']);z['coverage'].append(r['nextOppositeRepair']['repairQty']/r['overflowAllocation'] if r['overflowAllocation']>EPS else None)
 for z in routes.values():
  z['within30Share']=z['within30']/z['n'];z['coversOverflowShare']=z['coversOverflow']/z['n'];z['medianLagSec']=med(z.pop('lags'));z['medianRepairToOverflow']=med(z.pop('coverage'))
 return {'compositeParents':n,'withNextOppositeRepair':len(withn),'within30':len(within30),'within30Share':len(within30)/n if n else None,'nextRouteCounts':{k:v['n'] for k,v in routes.items()},'routeGroups':routes,'medianLagSec':med([r['nextOppositeRepair']['lagSec'] for r in withn]),'nextRepairCoversOverflowShare':sum(r['nextOppositeRepair']['repairQty']+EPS>=r['overflowAllocation'] for r in withn)/len(withn) if withn else None,'medianNextRepairToOverflow':med([r['nextOppositeRepair']['repairQty']/r['overflowAllocation'] for r in withn if r['overflowAllocation']>EPS]),'rows':rows}

def main():
 v69=json.load(open('data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json',encoding='utf-8'));end_by={}
 for r in v69['conditionRows']:
  mid=int(r['marketId'])
  if mid not in end_by:
   s=r['ourState'];end_by[mid]=int(round(float(s['snapshotT'])+float(s['remainingSec'])*1000.0))
 stage=audit(build_components('data/target_wallet_official_v1.db',v71.STAGEA,end_by,True));fresh=audit(build_components('data/target_wallet_official_v1.db',[1912961],None,False))
 out={'version':'TARGET_ETH_V86_COMPOSITE_RELAY_EXECUTION_ROUTE','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'stageA16Pre180':stage,'fresh1912961':fresh,'interpretationBoundary':['Target actual fills only','next opposite Repair strictly later than composite clock','same-clock ordering excluded','route is descriptive execution evidence, not runtime action authority','no winner/PnL']};p='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V86_COMPOSITE_RELAY_EXECUTION_ROUTE_20260903.json';Path(p).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'stage':{k:v for k,v in stage.items() if k!='rows'},'fresh1912961':{k:v for k,v in fresh.items() if k!='rows'}},ensure_ascii=False))
if __name__=='__main__':main()
