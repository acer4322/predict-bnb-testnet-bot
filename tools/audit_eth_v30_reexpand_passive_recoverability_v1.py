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
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class AuditSim(v30.DirectionalThesisCycleSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.reexpandStates=[];self._seenReexpandFillCount=0;self._lastQv=None
 def process(self,t):
  before=self.reexpandActualFills;super().process(t)
  if self.reexpandActualFills<=before:return
  rb=self.reserveBuilder
  if rb is None:return
  qv=v1.quotes(self.book)
  if not qv:return
  side=rb.get('firstSide');repair='DOWN' if side=='UP' else 'UP';fp=float(rb.get('firstPrice') or 0.0);ceiling=1.0-fp-0.01
  rp=float(qv[repair]['bid']);ra=float(qv[repair]['ask']);tp=float(qv[side]['bid']);ta=float(qv[side]['ask'])
  floor,u,d,cost=self._raw_floor();native_depth=0.0
  try:
   fake={'side':repair,'price':ceiling};native_depth=float(self._native_level_depth(fake))
  except Exception:pass
  st={'t':int(t),'side':side,'repairSide':repair,'secondsLeft':(int(self.capEnd)-int(t))/1000.0,'firstPrice':fp,'repairCeiling':ceiling,'repairBid':rp,'repairAsk':ra,'thesisBid':tp,'thesisAsk':ta,'pairBidSum':fp+rp,'pairAskSum':fp+ra,'repairBidToCeilingTicks':(ceiling-rp)*100.0,'repairAskToCeilingTicks':(ra-ceiling)*100.0,'repairCeilingDepth':native_depth,'bookImbalance':float(qv.get('imb') or 0.0),'bidDepth':float(qv.get('bd') or 0.0),'askDepth':float(qv.get('ad') or 0.0),'top3Bid':float(qv.get('tb') or 0.0),'top3Ask':float(qv.get('ta') or 0.0),'floorAfterReexpandFill':float(floor),'bestPayoffAfterReexpandFill':float(max(u,d)-cost),'absNetAfterReexpandFill':abs(float(u)-float(d)),'priorRecoveries':int(self.payoffRepairCompletions),'priorQueueProgressEvents':int(self.queueProgressEvents),'priorQueueLeaseExtensions':int(self.queueLeaseExtensions),'reexpandSubmitToFillMs':int(t)-int(rb.get('submittedAt') or t)}
  self.reexpandStates.append(st)
 def run_audit(self,models,winner):
  r=super().run_exam_v30(models,winner)
  # Label each materialized re-expand state by whether a later PAYOFF_RECOVERED event exists after its t.
  rec_times=[int(x['t']) for x in r.get('thesisEvents',[]) if x.get('event')=='PAYOFF_RECOVERED']
  for s in self.reexpandStates:s['laterPayoffRecovered']=any(rt>int(s['t']) for rt in rec_times)
  return r

def load_runtime(a):
 models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);return models,life,cap,tim,econ,price,sur

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v30_recoverability_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=load_runtime(a);rows=[];states=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=AuditSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_audit(models,cr['winner']);ss=list(sim.reexpandStates)
   finally:sim.close()
   for s in ss:s['marketId']=mid;states.append(s)
   rows.append({'marketId':mid,'reexpandStates':ss,'finalFloor':r['floor'],'finalAbsNet':r['absNet'],'recoveries':r['payoffRepairCompletions']});print(json.dumps({'marketId':mid,'states':ss,'finalFloor':r['floor']},ensure_ascii=False),flush=True)
  out={'version':'ETH_V30_REEXPAND_PASSIVE_RECOVERABILITY_AUDIT_V1','researchOnly':True,'states':states,'rows':rows,'boundary':['consumed realistic HFT only','features captured after re-expand actual fill and before subsequent Repair decisions','winner/PnL excluded from recoverability label','no behavior change']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'states':states},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
