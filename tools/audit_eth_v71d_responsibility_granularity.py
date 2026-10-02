from __future__ import annotations
import argparse,json,sqlite3,statistics,math,importlib.util,sys
from pathlib import Path
EPS=1e-9
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('v71base',Path(__file__).with_name('audit_eth_v71_generation_economic_anatomy.py'));v71=importlib.util.module_from_spec(spec);spec.loader.exec_module(v71)

def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return statistics.median(xs) if xs else None

def group_responsibilities(components):
 by={}
 for x in components:by.setdefault(int(x['marketId']),[]).append(x)
 resps=[];per=[]
 for mid,ev in by.items():
  ev=sorted(ev,key=lambda z:(int(z['t']),0 if z['kind']=='REPAIR' else 1,str(z.get('key',''))))
  cur=None;market=[];expand_components=repair_components=0;flip_with_residual=0
  for x in ev:
   kind=x['kind'];side=x['side'];q=float(x['qty']);t=int(x['t'])
   if kind=='EXPAND':
    expand_components+=1
    if cur is None or side!=cur['side']:
     prev=cur
     if prev is not None:
      prev['closedAt']=t;prev['closedBySideFlip']=True;prev['residualAtSideFlip']=max(0.,float(prev['debt']))
      if prev['residualAtSideFlip']>1e-7:flip_with_residual+=1
     cur={'marketId':mid,'responsibilityIndex':len(market)+1,'side':side,'paySide':'DOWN' if side=='UP' else 'UP','bornAt':t,'debt':0.0,'expandQty':0.0,'expandNotional':0.0,'expandPayments':0,'repairPayments':0,'repairPaid':0.0,'routes':set(),'closedAt':None,'closedBySideFlip':False,'residualAtSideFlip':None}
     market.append(cur);resps.append(cur)
    cur['debt']+=q;cur['expandQty']+=q;cur['expandNotional']+=float(x.get('notional') or 0.0);cur['expandPayments']+=1;cur['routes'].add(str(x.get('route')))
   else:
    repair_components+=1
    if cur is not None and side==cur['paySide'] and cur['debt']>EPS:
     pay=min(q,cur['debt']);cur['debt']-=pay;cur['repairPaid']+=pay;cur['repairPayments']+=1
  for r in market:
   r['residualEnd']=max(0.,float(r['debt']));r['fullyPaidEnd']=r['residualEnd']<=1e-7;r['mixedPassiveActive']=('MAKER' in r['routes'] and 'TAKER' in r['routes']) or any('PASSIVE' in z for z in r['routes']) and any('ACTIVE' in z for z in r['routes']);r['routes']=sorted(r['routes'])
  per.append({'marketId':mid,'expandComponents':expand_components,'repairComponents':repair_components,'responsibilityBirths':len(market),'sameSideExtraExpandPayments':max(0,expand_components-len(market)),'sideFlipWithResidualDebt':flip_with_residual,'responsibilitiesWithMultiExpand':sum(r['expandPayments']>1 for r in market),'mixedRouteResponsibilities':sum(r['mixedPassiveActive'] for r in market)})
 n=len(resps);ec=sum(x['expandComponents'] for x in per);rb=len(resps)
 summary={'markets':len(per),'expandComponents':ec,'repairComponents':sum(x['repairComponents'] for x in per),'responsibilityBirths':rb,'expandComponentsPerResponsibility':ec/rb if rb else None,'sameSideExtraExpandPayments':ec-rb,'sameSideExtraExpandPaymentShare':(ec-rb)/ec if ec else None,'responsibilitiesWithMultiExpand':sum(r['expandPayments']>1 for r in resps),'multiExpandResponsibilityShare':sum(r['expandPayments']>1 for r in resps)/rb if rb else None,'mixedPassiveActiveResponsibilities':sum(r['mixedPassiveActive'] for r in resps),'mixedPassiveActiveResponsibilityShare':sum(r['mixedPassiveActive'] for r in resps)/rb if rb else None,'medianExpandPaymentsPerResponsibility':med([r['expandPayments'] for r in resps]),'medianRepairPaymentsPerResponsibility':med([r['repairPayments'] for r in resps]),'sideFlipWithResidualDebt':sum(x['sideFlipWithResidualDebt'] for x in per),'fullyPaidAtEndShare':sum(r['fullyPaidEnd'] for r in resps)/rb if rb else None,'perMarket':per}
 return summary,resps

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--target-db',default='data/target_wallet_official_v1.db');ap.add_argument('--v69',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json');ap.add_argument('--v70f',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V70F_EARLY_CLOCK_PARALLEL_RELAY_SMOKE_20260903.json');ap.add_argument('--output',required=True);a=ap.parse_args()
 v69=json.load(open(a.v69,encoding='utf-8'));end_by={}
 for r in v69['conditionRows']:
  mid=int(r['marketId'])
  if mid not in end_by:
   s=r['ourState'];end_by[mid]=int(round(float(s['snapshotT'])+float(s['remainingSec'])*1000.))
 con=sqlite3.connect(a.target_db);ph=','.join('?'*len(v71.STAGEA));rows=con.execute(f"select market_id,parent_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",v71.STAGEA).fetchall();con.close()
 tc=v71.target_components(rows,end_by,pre180=True);v70f=json.load(open(a.v70f,encoding='utf-8'));oc=v71.our_components(v70f)
 ts,tr=group_responsibilities(tc);os,orr=group_responsibilities(oc)
 cand=v70f['rows'][0]['candidate'];eligible=[x for x in cand.get('v70fRepairSubmitClockRows',[]) if x.get('submit') and x.get('objectiveId') is not None];obj_ids=sorted(set(str(x['objectiveId']) for x in eligible))
 v70f_active_expand=sum(1 for x in cand.get('v53FillEvents',[]) if x.get('role')=='ACTIVE_EXPAND')
 comparison={'targetExpandComponentsPerResponsibility':ts['expandComponentsPerResponsibility'],'ourExpandComponentsPerResponsibility':os['expandComponentsPerResponsibility'],'targetSameSideExtraExpandPaymentShare':ts['sameSideExtraExpandPaymentShare'],'ourSameSideExtraExpandPaymentShare':os['sameSideExtraExpandPaymentShare'],'v70fIndependentEarlyExpandObjectiveIds':len(obj_ids),'v70fActiveExpandFillComponents':v70f_active_expand,'v70fEconomicallyDistinctResponsibilities':os['responsibilityBirths'],'v70fObjectiveToEconomicResponsibilityRatio':len(obj_ids)/max(1,os['responsibilityBirths'])}
 keep=ts['sameSideExtraExpandPaymentShare'] is not None and ts['sameSideExtraExpandPaymentShare']>0.25 and len(obj_ids)>=5 and os['responsibilityBirths']<len(obj_ids)
 decision='KEEP_PERSISTENT_GENERATION_RESPONSIBILITY_SEPARATE_FROM_EXECUTION_PAYMENTS' if keep else 'RESPONSIBILITY_GRANULARITY_NOT_YET_SEPARATING'
 out={'version':'ETH_REPAIR_V71D_RESPONSIBILITY_GRANULARITY_AUDIT','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'target':ts,'ourV70F':os,'comparison':comparison,'v70fIndependentObjectiveIds':obj_ids,'gates':{'targetHasSubstantialSameSidePaymentReuse':ts['sameSideExtraExpandPaymentShare']>0.25 if ts['sameSideExtraExpandPaymentShare'] is not None else False,'v70fCreatedManyIndependentExpandObjectives':len(obj_ids)>=5,'v70fObjectivesCollapseToFewerEconomicResponsibilities':os['responsibilityBirths']<len(obj_ids),'targetSideFlipResidualDebtZero':ts['sideFlipWithResidualDebt']==0},'decision':decision,'targetResponsibilities':tr,'ourResponsibilities':orr,'boundary':['economic responsibility grouped by persistent surplus side; same-side Passive/Active Expand are payments, not new generation births','pre-180 Target actual fills only','descriptive audit only','no controller change','no threshold tuning','no H100','no 8781']}
 Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'comparison':comparison,'gates':out['gates'],'target':{k:v for k,v in ts.items() if k!='perMarket'},'our':{k:v for k,v in os.items() if k!='perMarket'}},ensure_ascii=False))
if __name__=='__main__':main()
