from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15phase','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v14_strict_lane_source as v14
except ImportError:v14=sib('v14phase','run_eth_repair_functional_exam_v14_strict_lane_source.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13phase','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9phase','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
EPS=1e-9

class RepairPriceEnvelopeRuntime:
 def __init__(self,path):
  ck=joblib.load(path);self.version=ck.get('version');self.predictorFeatures=list(ck['predictorFeatures']);self.models=ck['models']
 def q80(self,phase,vals):
  z=self.models[phase];m=z['q80'];x=np.asarray([float(vals[k]) for k in self.predictorFeatures],np.float32).reshape(1,-1);return float(m.predict(x)[0])

class PhaseAwareEconomicAdmissionSim(v15.EconomicLaneAdmissionSim):
 def __init__(self,*a,price_envelope=None,**kw):
  super().__init__(*a,**kw);self.priceEnvelope=price_envelope
  self.basePriceEval=0;self.basePriceAccept=0;self.basePriceBlock=0;self.reservePriceEval=0;self.reservePriceAccept=0;self.reservePriceBlock=0
  self.reserveCheapAccept=0;self.reserveExpRecoveryEval=0;self.reserveExpRecoveryAccept=0;self.reserveExpRecoveryBlock=0
  self.expandValueEval=0;self.expandValueAccept=0;self.expandValueBlock=0;self.expandNegativeFloorBlock=0;self.parentAliveAtEconomicBlock=0
  self.baseMargins=[];self.reserveMargins=[];self.recoveryScores=[];self.expandScores=[]
 def _block_parent(self):self.parentAliveAtEconomicBlock+=int(self.repairParent is not None)
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is None:return False
  side,qty,_,role,oid=z;p=float(qv[side]['bid']);legal=1/p if p>EPS else 1e9
  if qty<legal-EPS:return v15.v14.StrictLaneSourceSim._submit_authorized(self,t,qv,z,roles_this_tick)
  q=min(max(qty,legal),12.);x,v=self._feature(t,side,q,p);self.econFeatureRows.append({'t':int(t),'role':role,'side':side,'pairEdge':v['pair_edge'],'pairSum':v['candidate_pair_sum'],'floorRatio':v['floor_ratio'],'netReserveRatio':v['net_pair_reserve_ratio']})
  if role=='REPAIR':
   phase='baseAcquisitionRepair' if v['net_pair_reserve_ratio']<=0 else 'reserveRepair';env=self.priceEnvelope.q80(phase,v);margin=float(env-v['candidate_pair_sum'])
   if phase=='baseAcquisitionRepair':
    self.basePriceEval+=1;self.baseMargins.append(margin)
    if v['candidate_pair_sum']>env+EPS:self.basePriceBlock+=1;self._block_parent();return False
    self.basePriceAccept+=1
   else:
    self.reservePriceEval+=1;self.reserveMargins.append(margin)
    if v['candidate_pair_sum']>env+EPS:self.reservePriceBlock+=1;self._block_parent();return False
    self.reservePriceAccept+=1
    if v['match_fraction']>0 and v['pair_edge']>=-EPS:
     self.reserveCheapAccept+=1
    else:
     sc,th=self.economic.score('expensive_repair_recovers_30s',x);self.reserveExpRecoveryEval+=1;self.recoveryScores.append(sc)
     if sc<th:self.reserveExpRecoveryBlock+=1;self._block_parent();return False
     self.reserveExpRecoveryAccept+=1
  elif role=='EXPAND':
   u,d,cu,cd,cost,reserve,debt,un,hist=self._reconstruct_economics();floor=min(u,d)-cost
   if floor<-EPS:self.expandNegativeFloorBlock+=1;return False
   sc,th=self.economic.score('safe_expand_preserves_floor_30s',x);self.expandValueEval+=1;self.expandScores.append(sc)
   if sc<th:self.expandValueBlock+=1;return False
   self.expandValueAccept+=1
  return v15.v14.StrictLaneSourceSim._submit_authorized(self,t,qv,z,roles_this_tick)
 def run_exam_v16(self,models,winner):
  r=v15.v14.StrictLaneSourceSim.run_exam_v14(self,models,winner);r.update({
   'basePriceEval':self.basePriceEval,'basePriceAccept':self.basePriceAccept,'basePriceBlock':self.basePriceBlock,
   'reservePriceEval':self.reservePriceEval,'reservePriceAccept':self.reservePriceAccept,'reservePriceBlock':self.reservePriceBlock,
   'reserveCheapAccept':self.reserveCheapAccept,'reserveExpRecoveryEval':self.reserveExpRecoveryEval,'reserveExpRecoveryAccept':self.reserveExpRecoveryAccept,'reserveExpRecoveryBlock':self.reserveExpRecoveryBlock,
   'expandValueEval':self.expandValueEval,'expandValueAccept':self.expandValueAccept,'expandValueBlock':self.expandValueBlock,'expandNegativeFloorBlock':self.expandNegativeFloorBlock,
   'parentAliveAtEconomicBlock':self.parentAliveAtEconomicBlock,
   'baseMarginMean':float(np.mean(self.baseMargins)) if self.baseMargins else 0.,'baseMarginMin':min(self.baseMargins,default=0.),'baseMarginMax':max(self.baseMargins,default=0.),
   'reserveMarginMean':float(np.mean(self.reserveMargins)) if self.reserveMargins else 0.,'recoveryScoreMean':float(np.mean(self.recoveryScores)) if self.recoveryScores else 0.,'expandScoreMean':float(np.mean(self.expandScores)) if self.expandScores else 0.
  });return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--market-ids',default='1817824,1818265,1818307,1818920,1820056,1820148');ap.add_argument('--scenarios',default='CONTROL');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v16_phase_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');timing=v13.Wait10Runtime(a.timing_model);economic=v15.EconomicLaneValueRuntime(a.economic_model);price=RepairPriceEnvelopeRuntime(a.price_model);by={int(r['marketId']):r for r in cohort if r['split']!='TRAIN40'};ids=[int(x) for x in a.market_ids.split(',') if x.strip()];names=[x.strip() for x in a.scenarios.split(',') if x.strip()];rows=[]
  for sc in names:
   cfg=ex1.SCENARIOS[sc]
   for mid in ids:
    cr=by[mid];sim=PhaseAwareEconomicAdmissionSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap,timing=timing,economic=economic,price_envelope=price)
    try:r=sim.run_exam_v16(models,cr['winner']);c=sim.causal()
    finally:sim.close()
    rows.append({'scenario':sc,'marketId':mid,'functional':r,'causal':c});print(json.dumps({'marketId':mid,'base':f"{r['basePriceAccept']}/{r['basePriceEval']}",'baseBlock':r['basePriceBlock'],'reserve':f"{r['reservePriceAccept']}/{r['reservePriceEval']}",'reserveRecovery':f"{r['reserveExpRecoveryAccept']}/{r['reserveExpRecoveryEval']}",'expand':f"{r['expandValueAccept']}/{r['expandValueEval']}",'negFloorExpand':r['expandNegativeFloorBlock'],'pnl':r['pnlDiagnosticOnly'],'drift':r['repairToExpandAtFirstFill'],'overOwned':r['overOwnedSubmitViolations']}),flush=True)
  keys=['basePriceEval','basePriceAccept','basePriceBlock','reservePriceEval','reservePriceAccept','reservePriceBlock','reserveCheapAccept','reserveExpRecoveryEval','reserveExpRecoveryAccept','reserveExpRecoveryBlock','expandValueEval','expandValueAccept','expandValueBlock','expandNegativeFloorBlock','parentAliveAtEconomicBlock','crossLaneRepairFallbackBlocks','crossLaneExpandFallbackBlocks','timingRepairSubmits','repairToExpandAtFirstFill','authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations']
  agg={k:sum(int(x['functional'].get(k) or 0) for x in rows) for k in keys};agg['unresolvedCarrierQty']=sum(float(x['functional']['unresolvedCarrierQty']) for x in rows);agg['prefillOpposite']=sum(int(x['causal']['oppositeBeforeFirstFill']) for x in rows);agg['sameSecond']=sum(int(x['causal']['sameSecond']) for x in rows)
  blocks=agg['basePriceBlock']+agg['reservePriceBlock']+agg['reserveExpRecoveryBlock'];gates={
   'basePriceExercised':agg['basePriceEval']>0,'basePriceHasAccept':agg['basePriceAccept']>0,'basePriceHasBlock':agg['basePriceBlock']>0,
   'reservePhaseExercised':agg['reservePriceEval']>0,'reserveDecisionHasEffect':agg['reservePriceAccept']+agg['reservePriceBlock']>0,
   'expandBranchExercised':agg['expandValueEval']+agg['expandNegativeFloorBlock']>0,
   'parentPersistsAcrossEconomicBlock':blocks==0 or agg['parentAliveAtEconomicBlock']==blocks,
   'zeroPrefillOpposite':agg['prefillOpposite']==0,'zeroSameSecond':agg['sameSecond']==0,'zeroCrossLaneRepairBypass':agg['crossLaneExpandFallbackBlocks']==0,
   'zeroRepairToExpand':agg['repairToExpandAtFirstFill']==0,'zeroTruthMismatch':agg['authorizedSubmitWithTruthRoleMismatch']==0,'zeroOverOwned':agg['overOwnedSubmitViolations']==0,'zeroUnresolved':abs(agg['unresolvedCarrierQty'])<=1e-9}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V16_PHASE_AWARE_ECONOMIC_ADMISSION','researchOnly':True,'performanceGraduationEligible':False,'priceTeacherVersion':price.version,'valueTeacherVersion':economic.version,'selectedMarketIds':ids,'aggregate':agg,'gates':gates,'smokeVerified':all(gates.values()),'rows':rows,'boundary':['consumed development only','actual HftBacktest fills','no model gradients','no threshold/PnL tuning','V14 strict lane source retained','Repair parent survives economic child block']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
