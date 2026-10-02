from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_repair_functional_exam_v30_directional_thesis_cycle as v30
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9; OFFSETS=(0,1000,3000,5000,10000)

class PathAuditSim(v30.DirectionalThesisCycleSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw); self.pathEpisodes=[]; self._activeEp=None; self._seenFillCount=0
 def _snap(self,t,ep,offset):
  qv=v1.quotes(self.book)
  if not qv:return None
  side=ep['side']; repair=ep['repairSide']; fp=ep['firstPrice']; ceiling=ep['repairCeiling']; rb=float(qv[repair]['bid']); ra=float(qv[repair]['ask'])
  unresolved=self.lane_unresolved('REPAIR'); carrier_prices=[]; carrier_qty=0.0; carrier_depth=0.0
  for key,e,rem in unresolved:
   p=float(self.orders.get(key,{}).get('price') or e.get('price') or 0.0); carrier_prices.append(p); carrier_qty+=float(rem)
   try: carrier_depth+=float(self._native_level_depth({'side':repair,'price':p}))
   except Exception: pass
  cp=max(carrier_prices) if carrier_prices else None
  floor,u,d,cost=self._raw_floor(); pb=self._repair_payoff_budget(rb)
  return {'offsetMs':int(offset),'t':int(t),'secondsLeft':(int(self.capEnd)-int(t))/1000.0,'repairBid':rb,'repairAsk':ra,'pairBidSum':fp+rb,'repairBidToCeilingTicks':(ceiling-rb)*100.0,'repairAskToCeilingTicks':(ra-ceiling)*100.0,'bookImbalance':float(qv.get('imb') or 0.0),'repairCarrierCount':len(unresolved),'repairCarrierQty':carrier_qty,'repairCarrierBestPrice':cp,'carrierBehindBestTicks':((rb-cp)*100.0 if cp is not None else None),'carrierDepthNow':carrier_depth,'queueProgressEvents':int(self.queueProgressEvents)-ep['queueProgress0'],'queueLeaseExtensions':int(self.queueLeaseExtensions)-ep['queueLease0'],'floor':float(floor),'bestPayoff':float(max(u,d)-cost),'absNet':abs(float(u)-float(d)),'payoffBudget':pb}
 def process(self,t):
  before=self.reexpandActualFills; super().process(t)
  if self.reexpandActualFills>before:
   rb=self.reserveBuilder
   if rb is not None:
    side=rb.get('firstSide'); repair='DOWN' if side=='UP' else 'UP'; fp=float(rb.get('firstPrice') or 0.0)
    self._activeEp={'fillT':int(t),'side':side,'repairSide':repair,'firstPrice':fp,'repairCeiling':1.0-fp-0.01,'queueProgress0':int(self.queueProgressEvents),'queueLease0':int(self.queueLeaseExtensions),'snaps':{},'recovered':False,'recoveryT':None}
    self.pathEpisodes.append(self._activeEp)
  ep=self._activeEp
  if ep is None:return
  for off in OFFSETS:
   if off not in ep['snaps'] and int(t)-ep['fillT']>=off:
    s=self._snap(t,ep,off)
    if s is not None: ep['snaps'][off]=s
  if self.payoffRepairCompletions>0 and self.lastPayoffRecoveryAt is not None and int(self.lastPayoffRecoveryAt)>ep['fillT']:
   ep['recovered']=True; ep['recoveryT']=int(self.lastPayoffRecoveryAt); self._activeEp=None
 def run_audit(self,models,winner):
  r=super().run_exam_v30(models,winner)
  return r

def load_runtime(a):
 models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);return models,life,cap,tim,econ,price,sur

def main():
 ap=argparse.ArgumentParser();
 for x in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']: ap.add_argument('--'+x,required=True)
 ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v30_recover_path_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=load_runtime(a);outrows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=PathAuditSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_audit(models,cr['winner']);eps=list(sim.pathEpisodes)
   finally:sim.close()
   for ep in eps: ep['snaps']=[ep['snaps'][k] for k in sorted(ep['snaps'])]
   row={'marketId':mid,'episodes':eps,'finalFloor':r['floor']};outrows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
  out={'version':'ETH_V30_REEXPAND_PASSIVE_RECOVERABILITY_PATH_V2','researchOnly':True,'rows':outrows,'boundary':['consumed realistic HFT only','all path features are receipt-clock strict-past at 0/1/3/5/10s after re-expand actual fill','winner/PnL excluded','no behavior change']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
 finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
