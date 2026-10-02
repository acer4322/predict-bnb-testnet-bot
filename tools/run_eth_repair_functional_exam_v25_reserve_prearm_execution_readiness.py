from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,os,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v23_queue_progress_lease as v23
except ImportError:v23=sib('v23for25','run_eth_repair_functional_exam_v23_queue_progress_lease.py')
EPS=1e-9

class ReservePrearmExecutionReadinessSim(v23.QueueProgressLeaseSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.reservePrearm=None;self.prearmBirths=0;self.prearmRefreshes=0;self.prearmInvalidations=0;self.prearmDepthProgressEvents=0;self.prearmPathBlockedEvents=0;self.prearmMaterializations=0;self.prearmEvents=[];self._nextPrearmId=1
 def _level_depth(self,side,p):
  if side=='UP':return float(self.book.get('bids',{}).get(round(float(p),12),self.book.get('bids',{}).get(float(p),0.0)) or 0.0)
  np=round(1.0-float(p),12);return float(self.book.get('asks',{}).get(np,self.book.get('asks',{}).get(1.0-float(p),0.0)) or 0.0)
 def _candidate(self,qv):
  floor,u,d,cost,absr,s=self._thin_balanced_package(qv)
  if self.repairParent is not None or self.outstanding_total()>EPS:return None
  if not (0<=floor<1.0 and absr<=.02 and s<1.-EPS):return None
  pu=float(qv['UP']['bid']);pd=float(qv['DOWN']['bid']);side='UP' if pu<pd-EPS else 'DOWN' if pd<pu-EPS else ('UP' if (self.n%2==0) else 'DOWN');p=float(qv[side]['bid']);q=1/p if p>EPS else 1e9
  if q<=EPS or q>12.+EPS:return None
  opp='DOWN' if side=='UP' else 'UP';ceiling=1.0-p-.01;opp_bid=float(qv[opp]['bid']);reachable=ceiling+EPS>=opp_bid
  return {'side':side,'price':p,'qty':q,'depth':self._level_depth(side,p),'opp':opp,'ceiling':ceiling,'oppBestBid':opp_bid,'reachable':reachable,'floor':floor}
 def _start_reserve_builder(self,t,qv):
  # V25 replaces immediate first-leg submission with a virtual candidate. The actual V18/V23 Reserve Builder is created only on execution-readiness evidence.
  if self.reserveBuilder is not None:return False
  z=self._candidate(qv)
  if z is None:
   if self.reservePrearm is not None:
    self.prearmInvalidations+=1;self.prearmEvents.append({'atMs':int(t),'prearmId':self.reservePrearm['id'],'event':'PREARM_INVALIDATED'});self.reservePrearm=None
   return False
  if self.reservePrearm is None:
   z={**z,'id':self._nextPrearmId,'createdAt':int(t),'lastDepth':float(z['depth'])};self._nextPrearmId+=1;self.reservePrearm=z;self.prearmBirths+=1
   self.prearmEvents.append({'atMs':int(t),'prearmId':z['id'],'event':'PREARM_BORN','side':z['side'],'price':z['price'],'depth':z['depth'],'oppBestBid':z['oppBestBid'],'ceiling':z['ceiling'],'reachable':z['reachable']});return False
  p=self.reservePrearm
  if p['side']!=z['side'] or abs(float(p['price'])-float(z['price']))>1e-9:
   self.prearmRefreshes+=1;self.prearmEvents.append({'atMs':int(t),'prearmId':p['id'],'event':'PREARM_REFRESH','oldSide':p['side'],'oldPrice':p['price'],'newSide':z['side'],'newPrice':z['price']});p.update({k:z[k] for k in ('side','price','qty','depth','opp','ceiling','oppBestBid','reachable','floor')});p['lastDepth']=float(z['depth']);return False
  cur=float(z['depth']);prev=float(p.get('lastDepth') or 0.0);progress=cur<prev-EPS
  if progress:
   self.prearmDepthProgressEvents+=1;self.prearmEvents.append({'atMs':int(t),'prearmId':p['id'],'event':'FIRST_LEVEL_DEPLETION','side':z['side'],'price':z['price'],'prevDepth':prev,'depth':cur,'oppBestBid':z['oppBestBid'],'ceiling':z['ceiling'],'reachable':z['reachable']})
   if z['reachable']:
    pid=p['id'];self.reservePrearm=None;ok=super()._start_reserve_builder(t,qv)
    if ok:
     self.prearmMaterializations+=1;self.prearmEvents.append({'atMs':int(t),'prearmId':pid,'event':'FIRST_LEG_MATERIALIZED','side':z['side'],'price':z['price']})
    return bool(ok)
   self.prearmPathBlockedEvents+=1
  p.update({k:z[k] for k in ('qty','depth','opp','ceiling','oppBestBid','reachable','floor')});p['lastDepth']=cur
  return False
 def run_exam_v25(self,models,winner):
  r=super().run_exam_v23(models,winner);r.update({'prearmBirths':self.prearmBirths,'prearmRefreshes':self.prearmRefreshes,'prearmInvalidations':self.prearmInvalidations,'prearmDepthProgressEvents':self.prearmDepthProgressEvents,'prearmPathBlockedEvents':self.prearmPathBlockedEvents,'prearmMaterializations':self.prearmMaterializations,'prearmEvents':self.prearmEvents[:120]});return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v25_prearm_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v23.load_runtime(a);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];pair={}
   for name,cls,method in [('V23',v23.QueueProgressLeaseSim,'run_exam_v23'),('V25',ReservePrearmExecutionReadinessSim,'run_exam_v25')]:
    sim=cls(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
    try:r=getattr(sim,method)(models,cr['winner']);c=sim.causal()
    finally:sim.close()
    pair[name]={'functional':r,'causal':c}
   rows.append({'marketId':mid,**pair});print(json.dumps({'marketId':mid,'v23First':pair['V23']['functional']['reserveFirstLegActualFill'],'v23Cycles':pair['V23']['functional']['reserveCycleCompletion'],'v25First':pair['V25']['functional']['reserveFirstLegActualFill'],'v25Cycles':pair['V25']['functional']['reserveCycleCompletion'],'prearms':pair['V25']['functional']['prearmBirths'],'depthProgress':pair['V25']['functional']['prearmDepthProgressEvents'],'materialized':pair['V25']['functional']['prearmMaterializations'],'blocked':pair['V25']['functional']['prearmPathBlockedEvents']},ensure_ascii=False),flush=True)
  def sm(ver,k):return sum(float(x[ver]['functional'].get(k) or 0) for x in rows)
  base={'first':sm('V23','reserveFirstLegActualFill'),'cycles':sm('V23','reserveCycleCompletion'),'floorGain':sm('V23','reserveCycleFloorGainTotal')};new={'first':sm('V25','reserveFirstLegActualFill'),'cycles':sm('V25','reserveCycleCompletion'),'floorGain':sm('V25','reserveCycleFloorGainTotal'),'prearmBirths':sm('V25','prearmBirths'),'prearmRefreshes':sm('V25','prearmRefreshes'),'depthProgress':sm('V25','prearmDepthProgressEvents'),'pathBlocked':sm('V25','prearmPathBlockedEvents'),'materializations':sm('V25','prearmMaterializations')};base['uncompletedMaterialized']=max(0.,base['first']-base['cycles']);new['uncompletedMaterialized']=max(0.,new['first']-new['cycles']);base['completionPerFirst']=base['cycles']/base['first'] if base['first'] else None;new['completionPerFirst']=new['cycles']/new['first'] if new['first'] else None
  lost=[x['marketId'] for x in rows if x['V23']['functional']['reserveCycleCompletion']>0 and x['V25']['functional']['reserveCycleCompletion']<x['V23']['functional']['reserveCycleCompletion']];improved=[x['marketId'] for x in rows if x['V25']['functional']['reserveCycleCompletion']>x['V23']['functional']['reserveCycleCompletion']];pairmax=max((float(x['V25']['functional'].get('reserveCyclePairSumMax') or 0) for x in rows),default=0.)
  safety={'deterministicObligationCoversAllFirstFills':sm('V25','deterministicReserveObligationActivations')>=new['first'],'zeroRepairDrift':sm('V25','repairToExpandAtFirstFill')==0,'zeroTruthMismatch':sm('V25','authorizedSubmitWithTruthRoleMismatch')==0,'zeroOverOwned':sm('V25','overOwnedSubmitViolations')==0,'zeroUnresolved':abs(sm('V25','unresolvedCarrierQty'))<=EPS,'completedPairSumLt1':new['cycles']==0 or (pairmax>0 and pairmax<1.0)}
  behavior={'prearmExercised':new['prearmBirths']>0,'readinessProgressExercised':new['depthProgress']>0,'materializationNonzero':new['materializations']>0 and new['first']>0,'uncompletedMaterializedNotIncreased':new['uncompletedMaterialized']<=base['uncompletedMaterialized'],'completionEfficiencyNotWorse':new['completionPerFirst'] is not None and base['completionPerFirst'] is not None and new['completionPerFirst']+EPS>=base['completionPerFirst']}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V25_RESERVE_PREARM_EXECUTION_READINESS','researchOnly':True,'selectedMarketIds':[x['marketId'] for x in rows],'V23':base,'V25':new,'successfulControlCycleLossMarkets':lost,'improvedMarkets':improved,'maxCompletedPairSum':pairmax,'safetyGates':safety,'behaviorGates':behavior,'functionalSmokePass':all(safety.values()) and all(behavior.values()),'rows':rows,'boundary':['consumed realistic-HFT only','Maker-only virtual prearm before first-leg materialization','materialize only on same-level public depth decrease while opposite cheap path remains frontier-reachable','after actual first fill V20 deterministic obligation + V23 queue-progress lease unchanged','no fixed prearm TTL','no BTC numeric transfer','no PnL tuning']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalSmokePass':out['functionalSmokePass'],'V23':base,'V25':new,'lost':lost,'improved':improved,'safety':safety,'behavior':behavior},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
