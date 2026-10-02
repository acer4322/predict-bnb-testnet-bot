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

def clock_rows(components):
 by={}
 for x in components:
  key=(int(x['marketId']),int(x['t']),str(x['kind']),str(x['side']))
  z=by.setdefault(key,{'marketId':key[0],'t':key[1],'kind':key[2],'side':key[3],'qty':0.0,'floorDelta':0.0,'bestDelta':0.0,'notional':0.0,'routes':set()})
  z['qty']+=float(x['qty']);z['floorDelta']+=float(x['floorDelta']);z['bestDelta']+=float(x['bestDelta']);z['notional']+=float(x['notional']);z['routes'].add(str(x.get('route')))
 for z in by.values():z['routes']=sorted(z['routes'])
 return list(by.values())

def audit(components):
 clocks=clock_rows(components);markets=sorted(set(int(x['marketId']) for x in clocks));segments=[];resp_rows=[];ambiguous=0
 for mid in markets:
  ev=[x for x in clocks if int(x['marketId'])==mid];times=sorted(set(int(x['t']) for x in ev));cur=None;next_id=1
  for t in times:
   at=[x for x in ev if int(x['t'])==t];ex=[x for x in at if x['kind']=='EXPAND'];rp=[x for x in at if x['kind']=='REPAIR']
   ex_sides=set(x['side'] for x in ex)
   if len(ex_sides)>1:
    ambiguous+=1
    # same-clock opposite-side fills have no reliable parent ordering; do not score credit/replenishment here.
    cur=None;continue
   if ex:
    side=ex[0]['side'];spend=sum(max(0.0,-float(x['floorDelta'])) for x in ex);qty=sum(float(x['qty']) for x in ex);notional=sum(float(x['notional']) for x in ex);routes=sorted(set(r for x in ex for r in x['routes']))
    if cur is None or side!=cur['side']:
     if cur is not None:cur['closedAt']=t
     cur={'marketId':mid,'responsibilityId':next_id,'side':side,'paySide':'DOWN' if side=='UP' else 'UP','bornAt':t,'lastExpandAt':t,'repairCredit':0.0,'totalRepairCreditEarned':0.0,'totalReExpandSpend':0.0,'reExpandSegments':0,'negativeSegments':0,'closedAt':None};next_id+=1;resp_rows.append(cur)
    elif t>cur['lastExpandAt']:
     credit=float(cur['repairCredit']);coverage=credit/spend if spend>EPS else None;net=credit-spend
     seg={'marketId':mid,'responsibilityId':cur['responsibilityId'],'side':side,'t':t,'secondsSincePriorExpand':(t-cur['lastExpandAt'])/1000.0,'repairFloorCredit':credit,'expandFloorSpend':spend,'coverage':coverage,'netCreditAfterSpend':net,'expandQty':qty,'expandNotional':notional,'routes':routes}
     segments.append(seg);cur['totalReExpandSpend']+=spend;cur['reExpandSegments']+=1;cur['negativeSegments']+=int(net<-EPS);cur['repairCredit']=0.0;cur['lastExpandAt']=t
    # Any Repair on the same timestamp is excluded from credit by preregistered strict-clock rule.
   else:
    if cur is not None:
     for x in rp:
      if x['side']==cur['paySide']:
       gain=max(0.0,float(x['floorDelta']));cur['repairCredit']+=gain;cur['totalRepairCreditEarned']+=gain
 for r in resp_rows:r['netRecycledCredit']=r['totalRepairCreditEarned']-r['totalReExpandSpend']
 n=len(segments);cov=[s['coverage'] for s in segments if s['coverage'] is not None]
 summary={'markets':len(markets),'responsibilities':len(resp_rows),'reExpandSegments':n,'ambiguousOppositeExpandClocksExcluded':ambiguous,'medianCoverage':pct(cov,.5),'p25Coverage':pct(cov,.25),'p75Coverage':pct(cov,.75),'shareCoverageGe1':sum(float(x)>=1.0 for x in cov)/len(cov) if cov else None,'negativeSegmentShare':sum(float(s['netCreditAfterSpend'])<-EPS for s in segments)/n if n else None,'cumulativeRepairCredit':sum(float(s['repairFloorCredit']) for s in segments),'cumulativeReExpandSpend':sum(float(s['expandFloorSpend']) for s in segments),'cumulativeNetCredit':sum(float(s['netCreditAfterSpend']) for s in segments),'responsibilitiesNetCreditNonnegativeShare':sum(float(r['netRecycledCredit'])>=-EPS for r in resp_rows)/len(resp_rows) if resp_rows else None}
 return summary,segments,resp_rows

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--target-db',default='data/target_wallet_official_v1.db');ap.add_argument('--v69',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json');ap.add_argument('--v70f',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V70F_EARLY_CLOCK_PARALLEL_RELAY_SMOKE_20260903.json');ap.add_argument('--output',required=True);a=ap.parse_args()
 v69=json.load(open(a.v69,encoding='utf-8'));end_by={}
 for row in v69['conditionRows']:
  mid=int(row['marketId'])
  if mid not in end_by:
   s=row['ourState'];end_by[mid]=int(round(float(s['snapshotT'])+float(s['remainingSec'])*1000.))
 con=sqlite3.connect(a.target_db);ph=','.join('?'*len(v71.STAGEA));rows=con.execute(f"select market_id,parent_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",v71.STAGEA).fetchall();con.close()
 tc=v71.target_components(rows,end_by,pre180=True);oc=v71.our_components(json.load(open(a.v70f,encoding='utf-8')))
 ts,tseg,tr=audit(tc);os,oseg,orr=audit(oc)
 better=False
 if ts['medianCoverage'] is not None and os['medianCoverage'] is not None and ts['medianCoverage']>os['medianCoverage']+0.15:better=True
 if ts['negativeSegmentShare'] is not None and os['negativeSegmentShare'] is not None and ts['negativeSegmentShare']+0.15<os['negativeSegmentShare']:better=True
 decision='KEEP_FLOOR_CREDIT_RECYCLING_FOR_FUNCTIONAL_MICROWORLD' if better else 'FLOOR_CREDIT_CONSERVATION_NOT_SUFFICIENT_ANALYZE_REPLENISHMENT_STATE'
 out={'version':'ETH_REPAIR_V71F_FLOOR_CREDIT_RECYCLING','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'target':ts,'ourV70F':os,'comparison':{'targetMinusOurMedianCoverage':(ts['medianCoverage']-os['medianCoverage']) if ts['medianCoverage'] is not None and os['medianCoverage'] is not None else None,'targetMinusOurNegativeSegmentShare':(ts['negativeSegmentShare']-os['negativeSegmentShare']) if ts['negativeSegmentShare'] is not None and os['negativeSegmentShare'] is not None else None},'gate':{'targetMateriallyBetterFloorCreditRecycling':better},'decision':decision,'targetSegments':tseg,'ourSegments':oseg,'targetResponsibilities':tr,'ourResponsibilities':orr,'boundary':['same-clock Repair excluded from credit','same-clock opposite Expand clocks excluded as ordering ambiguous','coverage>=1 descriptive only','no runtime threshold authority','no PnL/winner','no controller change','no H100','no 8781']}
 Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'target':ts,'our':os,'comparison':out['comparison'],'ourSegments':oseg},ensure_ascii=False))
if __name__=='__main__':main()
