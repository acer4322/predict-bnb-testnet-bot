from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os,math
from pathlib import Path
from collections import deque
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v14_strict_lane_source as v14
except ImportError:v14=sib('v14econ','run_eth_repair_functional_exam_v14_strict_lane_source.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13econ','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9econ','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
EPS=1e-9

class EconomicLaneValueRuntime:
 def __init__(self,path):
  ck=joblib.load(path);self.features=list(ck['features']);self.models=ck['models'];self.version=ck.get('version')
 def score(self,name,x):
  z=self.models[name];return float(z['model'].predict_proba(np.asarray(x,np.float32).reshape(1,-1))[0,1]),float(z['threshold'])

class EconomicLaneAdmissionSim(v14.StrictLaneSourceSim):
 def __init__(self,*a,economic=None,**kw):
  super().__init__(*a,**kw);self.economic=economic
  self.econCheapRepairAccept=0;self.econExpensiveRepairEvaluated=0;self.econExpensiveRepairAccept=0;self.econExpensiveRepairBlock=0
  self.econExpandEvaluated=0;self.econExpandAccept=0;self.econExpandBlock=0;self.econExpandNegativeFloorBlock=0
  self.econRepairParentAliveAtBlock=0;self.econRepairBlockEvents=[];self.econScoresRepair=[];self.econScoresExpand=[];self.econFeatureRows=[]
 def _reconstruct_economics(self):
  u=d=cu=cd=cost=0.;reserve=debt=0.;un={'UP':deque(),'DOWN':deque()}
  hist=[]
  for h in self.authHist:
   side=str(h['side']);q=float(h['shares']);p=float(h['price']);t=int(h['time']);rel=int(h.get('rel') or 0)
   opp='DOWN' if side=='UP' else 'UP';left=q
   while left>EPS and un[opp]:
    oq,op=un[opp][0];z=min(left,oq);edge=z*(1.0-(op+p))
    if edge>=0:reserve+=edge
    else:debt+=-edge
    left-=z;oq-=z
    if oq<=EPS:un[opp].popleft()
    else:un[opp][0]=(oq,op)
   if left>EPS:un[side].append((left,p))
   if side=='UP':u+=q;cu+=q*p
   else:d+=q;cd+=q*p
   cost+=q*p;hist.append((t,rel,side,q,p))
  return u,d,cu,cd,cost,reserve,debt,un,hist
 def _feature(self,t,side,qty,price):
  u,d,cu,cd,cost,reserve,debt,un,hist=self._reconstruct_economics();gross=u+d;gap=abs(u-d);pair=min(u,d);paircov=2*pair/gross if gross>EPS else 1.;absratio=gap/gross if gross>EPS else 0.;floor=pair-cost;best=max(u,d)-cost;scale=max(cost,1.)
  weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;rel=1 if weak is not None and side==weak else -1 if weak is not None else 0
  au=cu/u if u>EPS else 0.;ad=cd/d if d>EPS else 0.;opp='DOWN' if side=='UP' else 'UP';left=max(0.,qty);mq=oc=ev=0.
  for oq,op in un[opp]:
   if left<=EPS:break
   z=min(left,oq);mq+=z;oc+=z*op;ev+=z*(1.-(op+price));left-=z
  oavg=oc/mq if mq>EPS else 0.;psum=oavg+price if mq>EPS else 0.;edge=ev/mq if mq>EPS else 0.;oqty=sum(q for q,_ in un[opp])
  pu=u+(qty if side=='UP' else 0.);pd=d+(qty if side=='DOWN' else 0.);pcost=cost+qty*price;pg=pu+pd;pp=min(pu,pd);pgap=abs(pu-pd);pfloor=pp-pcost;pbest=max(pu,pd)-pcost
  recent=[x for x in hist if t-x[0]<=30000];rr=sum(x[1]==1 for x in recent);ee=sum(x[1]==-1 for x in recent);last=hist[-1] if hist else None
  end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  vals={
   'seconds_left':(end-t)/1000.,'pair_coverage':paircov,'absnet_ratio':absratio,'gross_log':math.log1p(gross),'candidate_relation':float(rel),'candidate_qty_log':math.log1p(qty),
   'floor_ratio':floor/scale,'best_pnl_ratio':best/scale,'avg_cost_up':au,'avg_cost_down':ad,'avg_cost_gap':au-ad,'candidate_side_up':1. if side=='UP' else 0.,'candidate_price':price,'candidate_notional_ratio':qty*price/scale,
   'match_fraction':mq/max(qty,EPS),'opp_unmatched_ratio':oqty/max(gross,1.),'matched_opposite_avg_price':oavg,'candidate_pair_sum':psum,'pair_edge':edge,'pair_reserve_ratio':reserve/scale,'pair_debt_ratio':debt/scale,'net_pair_reserve_ratio':(reserve-debt)/scale,
   'projected_floor_delta_ratio':(pfloor-floor)/scale,'projected_best_delta_ratio':(pbest-best)/scale,'post_pair_coverage':2*pp/pg if pg>EPS else 1.,'post_absnet_ratio':pgap/pg if pg>EPS else 0.,
   'last_maker_age_log':math.log1p(min(300000,t-last[0] if last else 300000))/math.log1p(300000),'recent_repair_frac':rr/max(len(recent),1),'recent_expand_frac':ee/max(len(recent),1),'recent_maker_log':math.log1p(len(recent))/math.log1p(64)
  }
  x=[float(vals[k]) for k in self.economic.features];return x,vals
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is None:return False
  side,qty,_,role,oid=z
  p=float(qv[side]['bid']);legal=1/p if p>EPS else 1e9
  # Preserve pre-existing legal/budget handling; do not score impossible child.
  if qty<legal-EPS:return super()._submit_authorized(t,qv,z,roles_this_tick)
  q=min(max(qty,legal),12.);x,v=self._feature(t,side,q,p);self.econFeatureRows.append({'t':int(t),'role':role,'side':side,'pairEdge':v['pair_edge'],'pairSum':v['candidate_pair_sum'],'floorRatio':v['floor_ratio']})
  if role=='REPAIR':
   if v['match_fraction']>0 and v['pair_edge']>=-EPS:
    self.econCheapRepairAccept+=1
   else:
    sc,th=self.economic.score('expensive_repair_recovers_30s',x);self.econExpensiveRepairEvaluated+=1;self.econScoresRepair.append(sc)
    if sc<th:
     self.econExpensiveRepairBlock+=1;self.econRepairParentAliveAtBlock+=int(self.repairParent is not None);self.econRepairBlockEvents.append({'t':int(t),'score':sc,'threshold':th,'pairEdge':v['pair_edge'],'parentId':int(self.repairParent['id']) if self.repairParent else None});return False
    self.econExpensiveRepairAccept+=1
  elif role=='EXPAND':
   # Teacher is defined on pre-action non-loss floor. Negative floor cannot spend reserve for expansion.
   u,d,cu,cd,cost,reserve,debt,un,hist=self._reconstruct_economics();floor=min(u,d)-cost
   if floor<-EPS:
    self.econExpandNegativeFloorBlock+=1;return False
   sc,th=self.economic.score('safe_expand_preserves_floor_30s',x);self.econExpandEvaluated+=1;self.econScoresExpand.append(sc)
   if sc<th:self.econExpandBlock+=1;return False
   self.econExpandAccept+=1
  return super()._submit_authorized(t,qv,z,roles_this_tick)
 def run_exam_v15(self,models,winner):
  r=super().run_exam_v14(models,winner);r.update({
   'econCheapRepairAccept':self.econCheapRepairAccept,'econExpensiveRepairEvaluated':self.econExpensiveRepairEvaluated,'econExpensiveRepairAccept':self.econExpensiveRepairAccept,'econExpensiveRepairBlock':self.econExpensiveRepairBlock,
   'econExpandEvaluated':self.econExpandEvaluated,'econExpandAccept':self.econExpandAccept,'econExpandBlock':self.econExpandBlock,'econExpandNegativeFloorBlock':self.econExpandNegativeFloorBlock,
   'econRepairParentAliveAtBlock':self.econRepairParentAliveAtBlock,'econRepairScoreMean':float(np.mean(self.econScoresRepair)) if self.econScoresRepair else 0.,'econExpandScoreMean':float(np.mean(self.econScoresExpand)) if self.econScoresExpand else 0.,
   'econRepairBlockEvents':self.econRepairBlockEvents[:30],'econFeatureRows':self.econFeatureRows[:60]
  });return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--market-ids',default='1817824,1818007,1818750,1818920,1820056,1820148');ap.add_argument('--scenarios',default='CONTROL');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v15_econ_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');timing=v13.Wait10Runtime(a.timing_model);economic=EconomicLaneValueRuntime(a.economic_model);by={int(r['marketId']):r for r in cohort if r['split']!='TRAIN40'};ids=[int(x) for x in a.market_ids.split(',') if x.strip()];names=[x.strip() for x in a.scenarios.split(',') if x.strip()];rows=[]
  for sc in names:
   cfg=ex1.SCENARIOS[sc]
   for mid in ids:
    cr=by[mid];sim=EconomicLaneAdmissionSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap,timing=timing,economic=economic)
    try:r=sim.run_exam_v15(models,cr['winner']);c=sim.causal()
    finally:sim.close()
    row={'scenario':sc,'marketId':mid,'functional':r,'causal':c};rows.append(row);print(json.dumps({'marketId':mid,'cheapRepair':r['econCheapRepairAccept'],'expRepairEval':r['econExpensiveRepairEvaluated'],'expRepairAccept':r['econExpensiveRepairAccept'],'expRepairBlock':r['econExpensiveRepairBlock'],'expandEval':r['econExpandEvaluated'],'expandAccept':r['econExpandAccept'],'expandBlock':r['econExpandBlock'],'negFloorExpandBlock':r['econExpandNegativeFloorBlock'],'pnl':r['pnlDiagnosticOnly'],'drift':r['repairToExpandAtFirstFill'],'overOwned':r['overOwnedSubmitViolations']}),flush=True)
  agg={k:sum(int(x['functional'].get(k) or 0) for x in rows) for k in ['econCheapRepairAccept','econExpensiveRepairEvaluated','econExpensiveRepairAccept','econExpensiveRepairBlock','econExpandEvaluated','econExpandAccept','econExpandBlock','econExpandNegativeFloorBlock','econRepairParentAliveAtBlock','crossLaneRepairFallbackBlocks','crossLaneExpandFallbackBlocks','timingRepairSubmits','repairToExpandAtFirstFill','authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations']};agg['unresolvedCarrierQty']=sum(float(x['functional']['unresolvedCarrierQty']) for x in rows);agg['prefillOpposite']=sum(int(x['causal']['oppositeBeforeFirstFill']) for x in rows);agg['sameSecond']=sum(int(x['causal']['sameSecond']) for x in rows)
  gates={'cheapRepairExercised':agg['econCheapRepairAccept']>0,'expensiveRepairExercised':agg['econExpensiveRepairEvaluated']>0,'expensiveRepairDecisionHasEffect':agg['econExpensiveRepairAccept']+agg['econExpensiveRepairBlock']>0,'expandEconomicsExercised':agg['econExpandEvaluated']+agg['econExpandNegativeFloorBlock']>0,'repairParentPersistsAcrossEconomicBlock':agg['econExpensiveRepairBlock']==0 or agg['econRepairParentAliveAtBlock']==agg['econExpensiveRepairBlock'],'zeroPrefillOpposite':agg['prefillOpposite']==0,'zeroSameSecond':agg['sameSecond']==0,'zeroCrossLaneRepairBypass':agg['crossLaneExpandFallbackBlocks']==0,'zeroRepairToExpand':agg['repairToExpandAtFirstFill']==0,'zeroTruthMismatch':agg['authorizedSubmitWithTruthRoleMismatch']==0,'zeroOverOwned':agg['overOwnedSubmitViolations']==0,'zeroUnresolved':abs(agg['unresolvedCarrierQty'])<=1e-9}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V15_ECONOMIC_LANE_ADMISSION','researchOnly':True,'performanceGraduationEligible':False,'teacherVersion':economic.version,'selectedMarketIds':ids,'aggregate':agg,'gates':gates,'smokeVerified':all(gates.values()),'rows':rows,'boundary':['consumed development only','no model gradients','no threshold tuning','no winner/PnL feature','actual HftBacktest fills only','V14 strict lane ownership retained']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
