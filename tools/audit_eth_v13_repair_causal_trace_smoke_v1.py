from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13trace','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9trace','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
EPS=1e-9

class CausalTraceSim(v13.AnchoredWait10Sim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.submitTrace=[];self.fillTrace=[];self.parentBirthTrace=[]
 def submit(self,t,side,p,q):
  role=getattr(self,'_pendingAuthorizedRole',None);pid=getattr(self,'_pendingParentId',None);lane=getattr(self,'_pendingLane',None)
  ok=super().submit(t,side,p,q)
  if ok:self.submitTrace.append({'t':int(t),'side':side,'qty':float(q),'price':float(p),'pendingRole':role,'parentId':pid,'lane':lane})
  return ok
 def process(self,t):
  n0=len(self.authHist);super().process(t)
  if len(self.authHist)>n0:
   for x in self.authHist[n0:]:self.fillTrace.append({'t':int(x['time']),'side':x['side'],'qty':float(x['shares']),'price':float(x['price']),'rel':int(x['rel'])})
 def _maybe_birth_parent(self,t,weak,cap):
  b=self.repairParentBirths;super()._maybe_birth_parent(t,weak,cap)
  if self.repairParentBirths>b:self.parentBirthTrace.append({'t':int(t),'side':weak,'parentId':int(self.repairParent['id']) if self.repairParent else None})
 def causal_summary(self):
  first_submit=self.submitTrace[0] if self.submitTrace else None;first_fill=self.fillTrace[0] if self.fillTrace else None
  repairs=[x for x in self.submitTrace if x.get('pendingRole')=='REPAIR'];first_rep=repairs[0] if repairs else None;birth=self.parentBirthTrace[0] if self.parentBirthTrace else None
  ff=int(first_fill['t']) if first_fill else None;rs=int(first_rep['t']) if first_rep else None
  before=[x for x in self.submitTrace if ff is None or int(x['t'])<ff];through=[x for x in self.submitTrace if ff is not None and int(x['t'])<=ff]
  seed_side=first_submit['side'] if first_submit else None
  opposite_before=sum(1 for x in before if seed_side and x['side']!=seed_side);opposite_through=sum(1 for x in through if seed_side and x['side']!=seed_side)
  lag=None if ff is None or rs is None else rs-ff
  return {'firstSubmit':first_submit,'firstActualFill':first_fill,'firstRepairParentBirth':birth,'firstRepairSubmit':first_rep,'repairSubmitLagFromFirstActualFillMs':lag,'preFirstFillSubmitCount':len(before),'preFirstFillDistinctSides':sorted(set(x['side'] for x in before)),'oppositeSubmitBeforeFirstActualFill':opposite_before,'oppositeSubmitAtOrBeforeFirstActualFill':opposite_through,'sameTimestampRepairAsFirstFill':bool(ff is not None and rs==ff),'repairWithinFirstSecondAfterFill':bool(lag is not None and 0<=lag<1000),'repairBeforeFirstActualFill':bool(lag is not None and lag<0),'parentBornBeforeFirstActualFill':bool(birth and ff is not None and int(birth['t'])<ff),'submitTrace':self.submitTrace[:30],'fillTrace':self.fillTrace[:30],'parentBirthTrace':self.parentBirthTrace[:10]}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--market-ids',default='1818007,1820056,1820148');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v13_trace_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');timing=v13.Wait10Runtime(a.timing_model);test={int(r['marketId']):r for r in cohort if r['split']!='TRAIN40'};ids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
  for mid in ids:
   cr=test[mid];sim=CausalTraceSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=timing)
   try:
    r=sim.run_exam_v13(models,cr['winner']);c=sim.causal_summary()
   finally:sim.close()
   gates={'hasSeedSubmit':c['firstSubmit'] is not None,'hasSeedActualFill':c['firstActualFill'] is not None,'hasRepairParentBirth':c['firstRepairParentBirth'] is not None,'hasRepairSubmit':c['firstRepairSubmit'] is not None,'noOppositeBeforeFirstFill':c['oppositeSubmitBeforeFirstActualFill']==0,'noOppositeAtOrBeforeFirstFill':c['oppositeSubmitAtOrBeforeFirstActualFill']==0,'repairStrictlyAfterFirstFill':c['repairSubmitLagFromFirstActualFillMs'] is not None and c['repairSubmitLagFromFirstActualFillMs']>0,'notSameTimestamp':not c['sameTimestampRepairAsFirstFill'],'notWithinFirstSecondForWait10Trace':not c['repairWithinFirstSecondAfterFill'],'parentNotBornBeforeFill':not c['parentBornBeforeFirstActualFill'],'noRepairToExpand':r['repairToExpandAtFirstFill']==0,'noOverOwned':r['overOwnedSubmitViolations']==0}
   rows.append({'marketId':mid,'gates':gates,'pass':all(gates.values()),'causal':c,'functional':{'repairParentBirths':r['repairParentBirths'],'repairParentCompletions':r['repairParentCompletions'],'timingWait10Schedules':r['timingWait10Schedules'],'timingDeadlineFires':r['timingDeadlineFires'],'timingRepairSubmits':r['timingRepairSubmits'],'pairCoverage':r['pairCoverage'],'absNet':r['absNet']}});print(json.dumps({'marketId':mid,'pass':all(gates.values()),'lagMs':c['repairSubmitLagFromFirstActualFillMs'],'preFillSides':c['preFirstFillDistinctSides'],'repairSubmits':r['timingRepairSubmits']},ensure_ascii=False),flush=True)
  agg={'markets':len(rows),'passed':sum(r['pass'] for r in rows),'allPass':all(r['pass'] for r in rows),'minRepairLagMs':min((r['causal']['repairSubmitLagFromFirstActualFillMs'] for r in rows if r['causal']['repairSubmitLagFromFirstActualFillMs'] is not None),default=None),'sameTimestampViolations':sum(r['causal']['sameTimestampRepairAsFirstFill'] for r in rows),'sameSecondViolations':sum(r['causal']['repairWithinFirstSecondAfterFill'] for r in rows),'preFillOppositeSubmitViolations':sum(r['causal']['oppositeSubmitBeforeFirstActualFill'] for r in rows),'parentBeforeFillViolations':sum(r['causal']['parentBornBeforeFirstActualFill'] for r in rows)}
  out={'version':'ETH_V13_REPAIR_CAUSAL_TRACE_SMOKE_V1','researchOnly':True,'realisticHftBacktest':True,'aggregate':agg,'rows':rows,'boundary':['actual HftBacktest fills only','current V13 code path','no dream/synthetic fills','causal anti-cheat functional evidence only','PnL not a pass gate']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
