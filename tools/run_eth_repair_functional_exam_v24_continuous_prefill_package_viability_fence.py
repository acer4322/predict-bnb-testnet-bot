from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,os,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v23_queue_progress_lease as v23
except ImportError:v23=sib('v23for24','run_eth_repair_functional_exam_v23_queue_progress_lease.py')
EPS=1e-9

class ContinuousPrefillPackageViabilityFenceSim(v23.QueueProgressLeaseSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw)
  self.prefillViabilityChecks=0;self.prefillPathBreakSignals=0;self.prefillPathBreakCancelRequests=0
  self.prefillTerminalRetirements=0;self.prefillLateFillAfterPathBreak=0;self.prefillPathEvents=[]
  self._invalidatedBuilders=set();self._lateFillCounted=set()
 def _prefill_viability(self):
  rb=self.reserveBuilder
  if rb is None or rb.get('firstFillAt') is not None:return None
  qv=v23.v1.quotes(self.book)
  if not qv:return None
  opp='DOWN' if rb['firstSide']=='UP' else 'UP'
  ceiling=1.0-float(rb['firstPrice'])-0.01
  best_bid=float(qv[opp]['bid'])
  return {'opp':opp,'ceiling':ceiling,'bestBid':best_bid,'reachable':bool(ceiling+EPS>=best_bid)}
 def cancel_expired(self,t):
  rb=self.reserveBuilder
  if rb is not None and rb.get('firstFillAt') is None:
   bid=int(rb['id']);key=rb['firstKey'];o=self.orders.get(key)
   if o is not None:
    try:s=self.snap(o)
    except Exception:s={}
    if v23.v1.live(s.get('status')):
     z=self._prefill_viability()
     if z is not None:
      self.prefillViabilityChecks+=1
      if not z['reachable'] and bid not in self._invalidatedBuilders:
       self._invalidatedBuilders.add(bid);self.prefillPathBreakSignals+=1
       self.prefillPathEvents.append({'builderId':bid,'atMs':int(t),'firstKey':key,'firstSide':rb['firstSide'],'firstPrice':float(rb['firstPrice']),'oppositeSide':z['opp'],'economicCeiling':float(z['ceiling']),'oppositeBestBid':float(z['bestBid']),'event':'PATH_BROKEN'})
      if bid in self._invalidatedBuilders and not bool(self.carrierLedger.get(key,{}).get('cancelRequested')):
       if self._cancel_key(t,key):
        self.prefillPathBreakCancelRequests+=1
        self.prefillPathEvents.append({'builderId':bid,'atMs':int(t),'firstKey':key,'event':'CANCEL_REQUESTED'})
  super().cancel_expired(t)
 def process(self,t):
  super().process(t);rb=self.reserveBuilder
  if rb is None:return
  bid=int(rb['id']);key=rb['firstKey'];e=self.carrierLedger.get(key)
  if bid in self._invalidatedBuilders and rb.get('firstFillAt') is not None and bid not in self._lateFillCounted:
   self._lateFillCounted.add(bid);self.prefillLateFillAfterPathBreak+=1
   self.prefillPathEvents.append({'builderId':bid,'atMs':int(t),'firstKey':key,'event':'LATE_FILL_AFTER_PATH_BREAK','firstFillAt':int(rb['firstFillAt'])})
  if bid in self._invalidatedBuilders and rb.get('firstFillAt') is None and e is not None:
   if bool(e.get('terminalConfirmed')) and float(e.get('actualFilled') or 0.0)<=EPS:
    self.prefillTerminalRetirements+=1
    self.prefillPathEvents.append({'builderId':bid,'atMs':int(t),'firstKey':key,'event':'UNMATERIALIZED_BUILDER_RETIRED','terminalStatus':e.get('terminalStatus')})
    self.reserveBuilder=None
 def run_exam_v24(self,models,winner):
  r=super().run_exam_v23(models,winner)
  r.update({'prefillViabilityChecks':self.prefillViabilityChecks,'prefillPathBreakSignals':self.prefillPathBreakSignals,'prefillPathBreakCancelRequests':self.prefillPathBreakCancelRequests,'prefillTerminalRetirements':self.prefillTerminalRetirements,'prefillLateFillAfterPathBreak':self.prefillLateFillAfterPathBreak,'prefillPathEvents':self.prefillPathEvents[:100]})
  return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v24_prefill_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v23.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];pair={}
   for name,cls,method in [('V23',v23.QueueProgressLeaseSim,'run_exam_v23'),('V24',ContinuousPrefillPackageViabilityFenceSim,'run_exam_v24')]:
    sim=cls(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
    try:r=getattr(sim,method)(models,cr['winner']);c=sim.causal()
    finally:sim.close()
    pair[name]={'functional':r,'causal':c}
   rows.append({'marketId':mid,**pair})
   print(json.dumps({'marketId':mid,'v23First':pair['V23']['functional']['reserveFirstLegActualFill'],'v23Cycles':pair['V23']['functional']['reserveCycleCompletion'],'v24First':pair['V24']['functional']['reserveFirstLegActualFill'],'v24Cycles':pair['V24']['functional']['reserveCycleCompletion'],'breaks':pair['V24']['functional']['prefillPathBreakSignals'],'cancels':pair['V24']['functional']['prefillPathBreakCancelRequests'],'retired':pair['V24']['functional']['prefillTerminalRetirements'],'lateFill':pair['V24']['functional']['prefillLateFillAfterPathBreak']},ensure_ascii=False),flush=True)
  def sm(ver,k):return sum(float(x[ver]['functional'].get(k) or 0) for x in rows)
  a23={'first':sm('V23','reserveFirstLegActualFill'),'cycles':sm('V23','reserveCycleCompletion'),'floorGain':sm('V23','reserveCycleFloorGainTotal')}
  a24={'first':sm('V24','reserveFirstLegActualFill'),'cycles':sm('V24','reserveCycleCompletion'),'floorGain':sm('V24','reserveCycleFloorGainTotal'),'breaks':sm('V24','prefillPathBreakSignals'),'cancels':sm('V24','prefillPathBreakCancelRequests'),'retired':sm('V24','prefillTerminalRetirements'),'lateFill':sm('V24','prefillLateFillAfterPathBreak')}
  a23['uncompletedMaterialized']=max(0.0,a23['first']-a23['cycles']);a24['uncompletedMaterialized']=max(0.0,a24['first']-a24['cycles'])
  decreased=[x['marketId'] for x in rows if x['V24']['functional']['reserveCycleCompletion']<x['V23']['functional']['reserveCycleCompletion']]
  improved=[x['marketId'] for x in rows if x['V24']['functional']['reserveCycleCompletion']>x['V23']['functional']['reserveCycleCompletion']]
  pairmax=max((float(x['V24']['functional'].get('reserveCyclePairSumMax') or 0) for x in rows),default=0.0)
  det=sm('V24','deterministicReserveObligationActivations');first=a24['first']
  safety={'deterministicObligationCoversAllActualFirstFills':det>=first,'zeroRepairDrift':sm('V24','repairToExpandAtFirstFill')==0,'zeroTruthMismatch':sm('V24','authorizedSubmitWithTruthRoleMismatch')==0,'zeroOverOwned':sm('V24','overOwnedSubmitViolations')==0,'zeroUnresolved':abs(sm('V24','unresolvedCarrierQty'))<=EPS,'completedPairSumLt1':a24['cycles']==0 or (pairmax>0 and pairmax<1.0),'lateFillAfterBreakStillOwned':sm('V24','prefillLateFillAfterPathBreak')<=det}
  behavior={'viabilityChecksExercised':sm('V24','prefillViabilityChecks')>0,'pathBreakExercised':a24['breaks']>0,'cancelExercised':a24['cancels']>0,'uncompletedMaterializedNotIncreased':a24['uncompletedMaterialized']<=a23['uncompletedMaterialized'],'noCycleRegression':len(decreased)==0}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V24_CONTINUOUS_PREFILL_PACKAGE_VIABILITY_FENCE','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'V23':a23,'V24':a24,'improvedMarkets':improved,'decreasedMarkets':decreased,'maxCompletedPairSum':pairmax,'safetyGates':safety,'behaviorGates':behavior,'smokeVerified':all(safety.values()) and all(behavior.values()),'rows':rows,'boundary':['consumed realistic-HFT only','Maker-only','V23 queue-progress lease preserved after actual first-leg fill','pre-fill PATH_BROKEN is binary economic-ceiling frontier reachability, not a fitted tick threshold','late fill after cancel request must create deterministic obligation','no BTC numeric transfer','no PnL tuning']}
  op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'V23':a23,'V24':a24,'improved':improved,'decreased':decreased,'safety':safety,'behavior':behavior},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
