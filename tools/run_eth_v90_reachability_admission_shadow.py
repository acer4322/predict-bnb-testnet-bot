from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).with_name(file); s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
g=sib('eth_v90_generic_for_shadow','run_eth_repair_modular_allocation_v2_generic_hft.py')
EPS=1e-9

def order_diag(sim,key,o):
 e=sim.carrierLedger.get(key,{}) if hasattr(sim,'carrierLedger') else {}
 st={}
 try: st=sim.snap(o) or {}
 except Exception: pass
 return {'key':key,'side':o.get('side'),'price':o.get('price'),'qty':o.get('qty'),'placed':o.get('placed'),'objectiveRole':o.get('objective_role'),'objectiveId':o.get('objective_id'),'executionRole':o.get('execution_role'),'status':st.get('status',o.get('status')),'cumExecQty':float(st.get('cumExecQty') or e.get('actualFilled') or o.get('cum') or 0.0),'leavesQty':float(st.get('leavesQty') or 0.0),'parentId':e.get('parentId'),'lane':e.get('lane'),'cancelRequested':key in getattr(sim,'cancelRequestedAt',{}),'terminalConfirmed':bool(e.get('terminalConfirmed'))}

def classify(r,orders):
 fills=int(r.get('actualFillEvents') or 0); births=int(r.get('repairParentBirths') or 0)
 repair_orders=[x for x in orders if str(x.get('objectiveRole') or '')=='REPAIR' or 'REPAIR' in str(x.get('lane') or '')]
 repair_fills=sum(float(x.get('cumExecQty') or 0)>EPS for x in repair_orders)
 if fills==0 and int(r.get('submits') or 0)>0:return 'ENTRY_SUBMITTED_NO_FILL'
 if fills>0 and births>0 and not repair_orders:return 'REPAIR_PARENT_BORN_NO_CARRIER'
 if repair_orders and repair_fills==0:return 'REPAIR_CARRIER_EXISTS_NO_FILL'
 if repair_orders and repair_fills>0:return 'REPAIR_CARRIER_MATERIALIZED'
 return 'OTHER'

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='v90_shadow_'));stop=threading.Event()
 def hb():
  while not stop.wait(10): print(json.dumps({'heartbeat':'V90_REACHABILITY_ADMISSION_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V90_SHADOW_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp); by={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
  models,life,cap,tim,econ,price,sur=g.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=g.ModularAllocationLedgerV2(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=g.v80.economic_v1_profile())
   try:
    r=sim.run_allocation_v2(models,cr['winner'])
    orders=[order_diag(sim,k,o) for k,o in sim.orders.items()]
    row={'marketId':mid,'classification':classify(r,orders),'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'floor':r.get('floor'),'submits':r.get('submits'),'actualFillEvents':r.get('actualFillEvents'),'repairParentBirths':r.get('repairParentBirths'),'shareSettlements':r.get('v80ShareRepairSettlements'),'thesisSide':r.get('thesisSide'),'thesisOpenSubmits':r.get('thesisOpenSubmits'),'thesisFirstActualFills':r.get('thesisFirstActualFills'),'packageRepairEval':r.get('packageRepairEval'),'packageRepairAccept':r.get('packageRepairAccept'),'packageRepairBlock':r.get('packageRepairBlock'),'repairParent':dict(sim.repairParent) if getattr(sim,'repairParent',None) else None,'reserveBuilder':dict(sim.reserveBuilder) if getattr(sim,'reserveBuilder',None) else None,'repairLaneObjective':dict(sim.repairLaneObjective) if getattr(sim,'repairLaneObjective',None) else None,'orders':orders,'placeHist':[list(x) for x in getattr(sim,'placeHist',[])],'carrierLedger':[dict(v) for v in getattr(sim,'carrierLedger',{}).values()],'modularEvents':r.get('modularRepairExecutionEvents',[]),'allocationEvents':r.get('allocationV2Events',[])}
   finally: sim.close()
   rows.append(row);print(json.dumps({'marketId':mid,'classification':row['classification'],'submits':row['submits'],'fills':row['actualFillEvents'],'repairBirths':row['repairParentBirths'],'orders':len(row['orders'])},ensure_ascii=False),flush=True)
  counts={}
  for r in rows:counts[r['classification']]=counts.get(r['classification'],0)+1
  out={'version':'ETH_V90_REACHABILITY_ADMISSION_SHADOW','date':'2026-09-03','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'counts':counts,'rows':rows,'boundary':['same frozen V90 candidate; instrumentation only','no Target runtime data','no tuning','use result to choose module seam, not threshold']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'counts':counts},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
