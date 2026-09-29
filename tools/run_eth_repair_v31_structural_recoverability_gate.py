from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_repair_functional_exam_v30_directional_thesis_cycle as v30
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
EPS=1e-9

class StructuralRecoverabilitySim(v30.DirectionalThesisCycleSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.structuralReexpandChecks=0;self.structuralReexpandBlocks=0;self.structuralReexpandPasses=0;self.structuralAudit=[]
 def _project_reexpand_repair(self,qv):
  if self.thesis is None:return {'feasible':True,'reason':'INITIAL_OPEN'}
  side=self._signal_side(qv)
  if side!=self.thesis['side']:return {'feasible':False,'reason':'THESIS_SIGNAL_MISMATCH'}
  p=float(qv[side]['bid']);q=1.0/p if p>EPS else 1e99
  if q<=EPS or q>12.+EPS:return {'feasible':False,'reason':'FIRST_LEG_QTY_INVALID'}
  floor,u,d,cost=self._raw_floor();hu=float(u)+(q if side=='UP' else 0.0);hd=float(d)+(q if side=='DOWN' else 0.0);hc=float(cost)+p*q;hfloor=min(hu,hd)-hc
  repair='DOWN' if side=='UP' else 'UP';ceiling=1.0-p-0.01;rbid=float(qv[repair]['bid']);rp=min(ceiling,rbid)
  gap=abs(hu-hd);deficit=max(0.0,-hfloor)
  if deficit<=EPS:return {'feasible':True,'reason':'NO_DEFICIT','side':side,'firstPrice':p,'firstQty':q,'hypFloor':hfloor,'room':gap}
  if rp<=EPS or rp>=1.0-EPS:return {'feasible':False,'reason':'NO_ADMISSIBLE_REPAIR_PRICE','side':side,'firstPrice':p,'firstQty':q,'hypFloor':hfloor,'room':gap,'repairPrice':rp,'repairCeiling':ceiling,'repairBid':rbid}
  need=deficit/(1.0-rp);legal=1.0/rp;req=max(need,legal);feasible=req<=gap+EPS
  return {'feasible':bool(feasible),'reason':'PASS' if feasible else 'LEGAL_OR_PAYOFF_QTY_EXCEEDS_ROOM','side':side,'firstPrice':p,'firstQty':q,'hypFloor':hfloor,'deficit':deficit,'room':gap,'repairSide':repair,'repairPrice':rp,'repairCeiling':ceiling,'repairBid':rbid,'needQty':need,'legalQty':legal,'requiredQty':req}
 def _start_reserve_builder(self,t,qv):
  if self.thesis is not None:
   self.structuralReexpandChecks+=1;a=self._project_reexpand_repair(qv);a={'t':int(t),**a};self.structuralAudit.append(a)
   if not a.get('feasible'):
    self.structuralReexpandBlocks+=1;return False
   self.structuralReexpandPasses+=1
  return super()._start_reserve_builder(t,qv)
 def run_exam_v31(self,models,winner):
  r=super().run_exam_v30(models,winner);r.update({'structuralReexpandChecks':self.structuralReexpandChecks,'structuralReexpandBlocks':self.structuralReexpandBlocks,'structuralReexpandPasses':self.structuralReexpandPasses,'structuralAudit':self.structuralAudit[:40]});return r

def load_runtime(a):
 models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);return models,life,cap,tim,econ,price,sur

def main():
 ap=argparse.ArgumentParser()
 for x in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+x,required=True)
 ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v31_structural_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=StructuralRecoverabilitySim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v31(models,cr['winner'])
   finally:sim.close()
   row={'marketId':mid,'floor':r['floor'],'absNet':r['absNet'],'recoveries':r['payoffRepairCompletions'],'reexpandSubmits':r['reexpandSubmits'],'reexpandActualFills':r['reexpandActualFills'],'cycles':r['thesisCycleCompletions'],'checks':r['structuralReexpandChecks'],'blocks':r['structuralReexpandBlocks'],'passes':r['structuralReexpandPasses'],'audit':r['structuralAudit'],'repairDrift':r['repairToExpandAtFirstFill'],'truthMismatch':r['authorizedSubmitWithTruthRoleMismatch'],'overOwned':r['overOwnedSubmitViolations'],'unresolved':r['unresolvedCarrierQty']};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
  out={'version':'ETH_REPAIR_V31_STRUCTURAL_RECOVERABILITY_GATE','researchOnly':True,'rows':rows,'safety':{'zeroRepairDrift':all(x['repairDrift']==0 for x in rows),'zeroTruthMismatch':all(x['truthMismatch']==0 for x in rows),'zeroOverOwned':all(x['overOwned']==0 for x in rows),'zeroUnresolved':all(abs(float(x['unresolved']))<=EPS for x in rows)},'boundary':['consumed realistic HFT only','gate applies only before re-expand','strict-past hypothetical payoff/legal-quantity geometry','no winner/PnL gate','no threshold fitting']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
