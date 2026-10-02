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

def q(xs,p):
 xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not xs:return None
 if len(xs)==1:return xs[0]
 z=(len(xs)-1)*p;i=int(z);f=z-i
 return xs[i]*(1-f)+xs[min(i+1,len(xs)-1)]*f

def annotate(components):
 by={}
 for x in components:by.setdefault(int(x['marketId']),[]).append(x)
 births=[]
 for mid,ev in by.items():
  ev=sorted(ev,key=lambda z:(int(z['t']),0 if z['kind']=='REPAIR' else 1,str(z.get('key',''))))
  open_g=[];repair_seen=False;hist=[]
  for x in ev:
   t=int(x['t']);kind=x['kind'];side=x['side'];qty=float(x['qty'])
   # Strict-past history excludes all same-timestamp components, including same physical fill Repair allocation.
   prior=[h for h in hist if int(h['t'])<t]
   if kind=='REPAIR':
    repair_seen=True;rem=qty
    for g in open_g:
     if rem<=EPS:break
     if g['paySide']!=side or g['remaining']<=EPS:continue
     pay=min(rem,g['remaining']);g['remaining']-=pay;rem-=pay
   elif repair_seen:
    debt=sum(max(0.,g['remaining']) for g in open_g)
    def wsum(k,sec):return sum(float(h['qty']) for h in prior if h['kind']==k and t-int(h['t'])<=sec*1000)
    r5,r15,r30=(wsum('REPAIR',z) for z in (5,15,30));e5,e15,e30=(wsum('EXPAND',z) for z in (5,15,30))
    rt=[int(h['t']) for h in prior if h['kind']=='REPAIR'];et=[int(h['t']) for h in prior if h['kind']=='EXPAND']
    load=debt+qty
    row={'marketId':mid,'t':t,'side':side,'route':x.get('route'),'qty':qty,'debtBeforeBirth':debt,'overlapAtBirth':debt>EPS,
         'repairQty5':r5,'repairQty15':r15,'repairQty30':r30,'expandQty5':e5,'expandQty15':e15,'expandQty30':e30,
         'secSinceRepair':(t-max(rt))/1000. if rt else None,'secSinceExpand':(t-max(et))/1000. if et else None,
         'totalDebtAfterBirth':load,
         'debtLoadToRepair15':load/r15 if r15>EPS else None,'debtLoadToRepair30':load/r30 if r30>EPS else None,
         'oldDebtToRepair15':debt/r15 if r15>EPS else None,
         'repairCoverage15':r15/load if load>EPS else None,'repairCoverage30':r30/load if load>EPS else None,
         'repairMinusExpand15':r15-e15,'repairMinusExpand30':r30-e30,
         'repairToExpand15':r15/e15 if e15>EPS else None,'repairToExpand30':r30/e30 if e30>EPS else None}
    births.append(row);open_g.append({'paySide':'DOWN' if side=='UP' else 'UP','remaining':qty})
   hist.append(x)
 return births

def summarize(rows):
 n=len(rows)
 def vals(k):return [r.get(k) for r in rows]
 return {'n':n,
  'medianRepairQty5':med(vals('repairQty5')),'medianRepairQty15':med(vals('repairQty15')),'medianRepairQty30':med(vals('repairQty30')),
  'medianExpandQty15':med(vals('expandQty15')),'medianExpandQty30':med(vals('expandQty30')),
  'medianSecSinceRepair':med(vals('secSinceRepair')),'medianSecSinceExpand':med(vals('secSinceExpand')),
  'medianDebtLoadToRepair15':med(vals('debtLoadToRepair15')),'p75DebtLoadToRepair15':q(vals('debtLoadToRepair15'),.75),
  'medianDebtLoadToRepair30':med(vals('debtLoadToRepair30')),'medianOldDebtToRepair15':med(vals('oldDebtToRepair15')),
  'medianRepairCoverage15':med(vals('repairCoverage15')),'p25RepairCoverage15':q(vals('repairCoverage15'),.25),
  'medianRepairCoverage30':med(vals('repairCoverage30')),
  'medianRepairMinusExpand15':med(vals('repairMinusExpand15')),'medianRepairMinusExpand30':med(vals('repairMinusExpand30')),
  'medianRepairToExpand15':med(vals('repairToExpand15')),'medianRepairToExpand30':med(vals('repairToExpand30')),
  'zeroRepair15Share':sum(float(r.get('repairQty15') or 0.0)<=EPS for r in rows)/n if n else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--target-db',default='data/target_wallet_official_v1.db');ap.add_argument('--v69',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json');ap.add_argument('--v70f',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V70F_EARLY_CLOCK_PARALLEL_RELAY_SMOKE_20260903.json');ap.add_argument('--output',required=True);a=ap.parse_args()
 v69=json.load(open(a.v69,encoding='utf-8'));end_by={}
 for r in v69['conditionRows']:
  mid=int(r['marketId'])
  if mid not in end_by:
   s=r['ourState'];end_by[mid]=int(round(float(s['snapshotT'])+float(s['remainingSec'])*1000.))
 con=sqlite3.connect(a.target_db);ph=','.join('?'*len(v71.STAGEA));rows=con.execute(f"select market_id,parent_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",v71.STAGEA).fetchall();con.close()
 tc=v71.target_components(rows,end_by,pre180=True);oc=v71.our_components(json.load(open(a.v70f,encoding='utf-8')))
 tb=annotate(tc);ob=annotate(oc);t_over=[x for x in tb if x['overlapAtBirth']];t_clean=[x for x in tb if not x['overlapAtBirth']];o_over=[x for x in ob if x['overlapAtBirth']]
 st,sc,so=summarize(t_over),summarize(t_clean),summarize(o_over)
 axes=[]
 if st['medianRepairCoverage15'] is not None and so['medianRepairCoverage15'] is not None and st['medianRepairCoverage15']>so['medianRepairCoverage15']*1.25:axes.append('HIGHER_STRICT_PAST_REPAIR15_COVERAGE')
 if st['medianDebtLoadToRepair15'] is not None and so['medianDebtLoadToRepair15'] is not None and st['medianDebtLoadToRepair15']*1.25<so['medianDebtLoadToRepair15']:axes.append('LOWER_TOTAL_DEBT_LOAD_TO_REPAIR15')
 if st['medianRepairMinusExpand15'] is not None and so['medianRepairMinusExpand15'] is not None and st['medianRepairMinusExpand15']>so['medianRepairMinusExpand15']+0.5:axes.append('STRONGER_REPAIR_MINUS_EXPAND15_BALANCE')
 decision='KEEP_REPAYMENT_CAPACITY_AS_FUNCTIONAL_MANAGEMENT_AXIS' if axes else 'REJECT_SIMPLE_REPAYMENT_CAPACITY_DIFFERENTIATOR'
 out={'version':'ETH_REPAIR_V71C_REPAYMENT_CAPACITY_ADMISSION','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'groups':{'targetOverlap':st,'targetClean':sc,'ourV70FOverlap':so},'candidateAxes':axes,'decision':decision,'rows':{'targetOverlap':t_over,'targetClean':t_clean,'ourV70FOverlap':o_over},'boundary':['all birth features strict-past; same-timestamp fill excluded','descriptive only','no runtime cutpoint authority','no winner/PnL','no threshold sweep','no H100','no 8781']}
 Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidateAxes':axes,'groups':out['groups']},ensure_ascii=False))
if __name__=='__main__':main()
