from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13strict','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9strict','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
EPS=1e-9

class StrictLaneSourceSim(v13.AnchoredWait10Sim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.crossLaneRepairFallbackBlocks=0;self.crossLaneExpandFallbackBlocks=0;self.authorizedRepairFromRepairSource=0;self.authorizedExpandFromExpandSource=0;self.submitTrace=[];self.fillTrace=[];self.parentBirthTrace=[]
 def choose_authorized(self,t,end,proposed_side,proposed_qty):
  ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
  requested='REPAIR' if weak is not None and proposed_side==weak else 'EXPAND' if dom is not None and proposed_side==dom else None
  z=super().choose_authorized(t,end,proposed_side,proposed_qty)
  if z is None:return None
  role=z[3]
  if requested=='EXPAND' and role!='EXPAND':self.crossLaneRepairFallbackBlocks+=1;return None
  if requested=='REPAIR' and role!='REPAIR':self.crossLaneExpandFallbackBlocks+=1;return None
  if requested=='REPAIR' and role=='REPAIR':self.authorizedRepairFromRepairSource+=1
  if requested=='EXPAND' and role=='EXPAND':self.authorizedExpandFromExpandSource+=1
  return z
 def submit(self,t,side,p,q):
  role=getattr(self,'_pendingAuthorizedRole',None);pid=getattr(self,'_pendingParentId',None);lane=getattr(self,'_pendingLane',None);ok=super().submit(t,side,p,q)
  if ok:self.submitTrace.append({'t':int(t),'side':side,'qty':float(q),'price':float(p),'pendingRole':role,'parentId':pid,'lane':lane})
  return ok
 def process(self,t):
  n0=len(self.authHist);super().process(t)
  if len(self.authHist)>n0:
   for x in self.authHist[n0:]:self.fillTrace.append({'t':int(x['time']),'side':x['side'],'qty':float(x['shares']),'price':float(x['price']),'rel':int(x['rel'])})
 def _maybe_birth_parent(self,t,weak,cap):
  b=self.repairParentBirths;super()._maybe_birth_parent(t,weak,cap)
  if self.repairParentBirths>b:self.parentBirthTrace.append({'t':int(t),'side':weak,'parentId':int(self.repairParent['id']) if self.repairParent else None})
 def run_exam_v14(self,models,winner):
  r=super().run_exam_v13(models,winner);r.update({'crossLaneRepairFallbackBlocks':self.crossLaneRepairFallbackBlocks,'crossLaneExpandFallbackBlocks':self.crossLaneExpandFallbackBlocks,'authorizedRepairFromRepairSource':self.authorizedRepairFromRepairSource,'authorizedExpandFromExpandSource':self.authorizedExpandFromExpandSource});return r
 def causal(self):
  fs=self.submitTrace[0] if self.submitTrace else None;ff=self.fillTrace[0] if self.fillTrace else None;rep=next((x for x in self.submitTrace if x.get('pendingRole')=='REPAIR'),None);birth=self.parentBirthTrace[0] if self.parentBirthTrace else None
  ft=int(ff['t']) if ff else None;rt=int(rep['t']) if rep else None;lag=None if ft is None or rt is None else rt-ft;seed=fs['side'] if fs else None
  return {'firstSubmit':fs,'firstActualFill':ff,'firstRepairParentBirth':birth,'firstRepairSubmit':rep,'repairLagMs':lag,'oppositeBeforeFirstFill':sum(1 for x in self.submitTrace if ft is not None and x['t']<ft and seed and x['side']!=seed),'oppositeAtOrBeforeFirstFill':sum(1 for x in self.submitTrace if ft is not None and x['t']<=ft and seed and x['side']!=seed),'sameTimestamp':bool(lag==0 if lag is not None else False),'sameSecond':bool(lag is not None and 0<=lag<1000),'submitTrace':self.submitTrace[:40],'fillTrace':self.fillTrace[:20]}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--market-ids',default='1817824,1818109,1818265,1818307');ap.add_argument('--scenarios',default='CONTROL');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v14_strict_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');timing=v13.Wait10Runtime(a.timing_model);by={int(r['marketId']):r for r in cohort if r['split']!='TRAIN40'};ids=[int(x) for x in a.market_ids.split(',') if x.strip()];names=[x.strip() for x in a.scenarios.split(',') if x.strip()];rows=[]
  for sc in names:
   cfg=ex1.SCENARIOS[sc]
   for mid in ids:
    cr=by[mid];sim=StrictLaneSourceSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap,timing=timing)
    try:r=sim.run_exam_v14(models,cr['winner']);c=sim.causal()
    finally:sim.close()
    row={'scenario':sc,'marketId':mid,'functional':r,'causal':c};rows.append(row);print(json.dumps({'scenario':sc,'marketId':mid,'crossLaneBlocks':r['crossLaneRepairFallbackBlocks'],'timingRepairSubmits':r['timingRepairSubmits'],'repairLagMs':c['repairLagMs'],'prefillOpposite':c['oppositeBeforeFirstFill'],'drift':r['repairToExpandAtFirstFill'],'overOwned':r['overOwnedSubmitViolations']}),flush=True)
  agg={'markets':len(rows),'crossLaneRepairFallbackBlocks':sum(x['functional']['crossLaneRepairFallbackBlocks'] for x in rows),'crossLaneExpandFallbackBlocks':sum(x['functional']['crossLaneExpandFallbackBlocks'] for x in rows),'authorizedRepairFromRepairSource':sum(x['functional']['authorizedRepairFromRepairSource'] for x in rows),'timingRepairSubmits':sum(x['functional']['timingRepairSubmits'] for x in rows),'prefillOppositeViolations':sum(x['causal']['oppositeBeforeFirstFill'] for x in rows),'oppositeAtOrBeforeFirstFillViolations':sum(x['causal']['oppositeAtOrBeforeFirstFill'] for x in rows),'sameTimestampViolations':sum(x['causal']['sameTimestamp'] for x in rows),'sameSecondViolations':sum(x['causal']['sameSecond'] for x in rows),'repairToExpand':sum(x['functional']['repairToExpandAtFirstFill'] for x in rows),'truthMismatch':sum(x['functional']['authorizedSubmitWithTruthRoleMismatch'] for x in rows),'overOwned':sum(x['functional']['overOwnedSubmitViolations'] for x in rows),'unresolvedQty':sum(float(x['functional']['unresolvedCarrierQty']) for x in rows)}
  g={'bypassPathExercisedAndBlocked':agg['crossLaneRepairFallbackBlocks']>0,'zeroPrefillOpposite':agg['prefillOppositeViolations']==0 and agg['oppositeAtOrBeforeFirstFillViolations']==0,'zeroSameTimestamp':agg['sameTimestampViolations']==0,'zeroSameSecond':agg['sameSecondViolations']==0,'repairOnlyFromRepairSource':agg['authorizedRepairFromRepairSource']>=agg['timingRepairSubmits'] and agg['crossLaneExpandFallbackBlocks']==0,'zeroRepairToExpand':agg['repairToExpand']==0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroUnresolved':abs(agg['unresolvedQty'])<=1e-9};out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V14_STRICT_LANE_SOURCE','researchOnly':True,'performanceGraduationEligible':False,'aggregate':agg,'gates':g,'allPass':all(g.values()),'rows':rows,'boundary':['actual HftBacktest fills only','no model gradients or threshold tuning','cross-lane proposal fallback is blocked','PnL diagnostic only','smoke-before-scale']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'aggregate':agg,'gates':g},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
