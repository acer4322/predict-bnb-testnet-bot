from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,os,math,statistics,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v23_queue_progress_lease as v23
except ImportError:v23=sib('v23quality','run_eth_repair_functional_exam_v23_queue_progress_lease.py')
try:
 from tools import run_eth_dagger60_smoke_v1 as v1
except ImportError:v1=sib('v1quality','run_eth_dagger60_smoke_v1.py')
EPS=1e-9; GRID=.01; INACTIVITY_MS=v23.INACTIVITY_MS

def qtile(xs,q):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return None
 z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
 return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

class QueueProgressQualityShadowSim(v23.QueueProgressLeaseSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self._qquality={}
 def _target_best_bid(self,side):
  if side=='UP':return float(max(self.book.get('bids',{}))) if self.book.get('bids') else None
  if side=='DOWN':return 1.0-float(min(self.book.get('asks',{}))) if self.book.get('asks') else None
  return None
 def _qrow(self,key,o,t,curd):
  q=self._qquality.get(key);bb=self._target_best_bid(o.get('side'));p=float(o.get('price') or 0.0)
  behind=max(0.0,(float(bb)-p)/GRID) if bb is not None else None
  if q is None:
   q={'key':key,'side':o.get('side'),'price':p,'placed':int(o.get('placed') or t),'firstSeenAt':int(t),'lastSeenAt':int(t),'initialDepth':float(curd),'lastDepth':float(curd),'minDepth':float(curd),'maxDepth':float(curd),'grossDepletion':0.0,'grossReplenishment':0.0,'depthDropEvents':0,'depthAddEvents':0,'checks':0,'atBestChecks':0,'behindChecks':0,'maxBehindTicks':0.0,'levelZeroSeen':False,'cancelAt':None};self._qquality[key]=q
  prev=float(q['lastDepth']);cur=float(curd);q['checks']+=1;q['lastSeenAt']=int(t);q['minDepth']=min(float(q['minDepth']),cur);q['maxDepth']=max(float(q['maxDepth']),cur)
  if cur<prev-EPS:q['grossDepletion']+=prev-cur;q['depthDropEvents']+=1
  elif cur>prev+EPS:q['grossReplenishment']+=cur-prev;q['depthAddEvents']+=1
  q['lastDepth']=cur;q['levelZeroSeen']=bool(q['levelZeroSeen'] or cur<=EPS)
  if behind is not None:
   q['maxBehindTicks']=max(float(q['maxBehindTicks']),float(behind))
   if behind<.5:q['atBestChecks']+=1
   else:q['behindChecks']+=1
  return q
 def cancel_expired(self,t):
  # Behavior is intentionally identical to V23; this override only records queue-quality state before applying the same lease logic.
  for key,o in self.orders.items():
   s=self.snap(o)
   if not v1.live(s.get('status')):continue
   if self._is_reserve_second_leg(key,o):
    curd=self._native_level_depth(o);self._qrow(key,o,t,curd);st=self._qlease.get(key)
    if st is None:
     st={'lastDepth':curd,'lastProgressAt':int(o.get('placed') or t),'placed':int(o.get('placed') or t),'progress':0,'extensions':0};self._qlease[key]=st;self.queueLeaseTracked+=1
    else:
     prev=float(st['lastDepth'])
     if curd<prev-EPS:
      st['lastProgressAt']=int(t);st['progress']+=1;self.queueProgressEvents+=1
      if int(t)-int(st['placed'])>=INACTIVITY_MS:st['extensions']+=1;self.queueLeaseExtensions+=1
     st['lastDepth']=curd
    age=int(t)-int(o.get('placed') or t);idle=int(t)-int(st['lastProgressAt'])
    if age>=INACTIVITY_MS and idle<INACTIVITY_MS:
     self.queueLeaseProtectedExpiryChecks+=1;continue
    if idle>=INACTIVITY_MS:
     if self._cancel_key(t,key):
      self.queueLeaseCancels+=1;self.queueLease.append({'key':key,'side':o.get('side'),'price':float(o.get('price') or 0.),'placed':int(o.get('placed') or 0),'cancelAt':int(t),'ageMs':age,'idleMs':idle,'progressEvents':int(st['progress']),'extensions':int(st['extensions'])})
      if key in self._qquality:self._qquality[key]['cancelAt']=int(t)
    continue
   if int(t)-int(o.get('placed') or t)>=INACTIVITY_MS:self._cancel_key(t,key)
 def quality_rows(self):
  self._refresh_carrier_ledger(int(self.meta.get('lastReceivedMs') or 0));out=[]
  for key,q0 in self._qquality.items():
   q=dict(q0);e=self.carrierLedger.get(key,{})
   init=max(float(q['initialDepth']),EPS);gd=float(q['grossDepletion']);gu=float(q['grossReplenishment']);checks=max(int(q['checks']),1)
   q.update({'actualFilled':float(e.get('actualFilled') or 0.0),'filled':float(e.get('actualFilled') or 0.0)>EPS,'terminalStatus':e.get('terminalStatus'),'cancelRequested':bool(e.get('cancelRequested')),'durationMs':int(q['lastSeenAt'])-int(q['firstSeenAt']),'maxPublicLevelDepletion':max(0.0,float(q['initialDepth'])-float(q['minDepth'])),'publicLevelDepletionFraction':max(0.0,float(q['initialDepth'])-float(q['minDepth']))/init,'grossDepletionFraction':gd/init,'grossReplenishmentFraction':gu/init,'replenishmentToDepletion':gu/max(gd,EPS),'netDepthChangeFraction':(float(q['lastDepth'])-float(q['initialDepth']))/init,'atBestReceiptFraction':float(q['atBestChecks'])/checks,'everBehind':int(q['behindChecks'])>0})
   st=self._qlease.get(key) or {};q['progressEvents']=int(st.get('progress') or 0);q['extensions']=int(st.get('extensions') or 0);out.append(q)
  return out

def summarize_quality(rows):
 def block(rr):
  return {'orders':len(rr),'markets':len({r['marketId'] for r in rr}),'filledRate':sum(bool(r['filled']) for r in rr)/len(rr) if rr else None,'levelZeroRate':sum(bool(r['levelZeroSeen']) for r in rr)/len(rr) if rr else None,'everBehindRate':sum(bool(r['everBehind']) for r in rr)/len(rr) if rr else None,'publicLevelDepletionFraction':stats([r['publicLevelDepletionFraction'] for r in rr]),'grossDepletionFraction':stats([r['grossDepletionFraction'] for r in rr]),'grossReplenishmentFraction':stats([r['grossReplenishmentFraction'] for r in rr]),'replenishmentToDepletion':stats([r['replenishmentToDepletion'] for r in rr]),'maxBehindTicks':stats([r['maxBehindTicks'] for r in rr]),'atBestReceiptFraction':stats([r['atBestReceiptFraction'] for r in rr]),'durationMs':stats([r['durationMs'] for r in rr]),'progressEvents':stats([r['progressEvents'] for r in rr]),'extensions':stats([r['extensions'] for r in rr])}
 return {'all':block(rows),'filled':block([r for r in rows if r['filled']]),'unfilled':block([r for r in rows if not r['filled']])}

def target_reference(path):
 if not path:return None
 d=json.load(open(path,encoding='utf-8'));rr=[r for r in d.get('rows',[]) if r.get('priceTimeResolved') and r.get('cheapPair') and r.get('placementMechanism')=='POSTFILL_NEW']
 out={}
 for asset in ('BTC','ETH'):
  z=[r for r in rr if r.get('asset')==asset]
  out[asset]={'episodes':len(z),'markets':len({r['marketId'] for r in z}),'publicLevelDepletionFraction':stats([r.get('publicLevelDepletionFraction') for r in z]),'levelZeroRate':sum(bool(r.get('orderLevelZeroSeen')) for r in z)/len(z) if z else None,'maxBehindTicks':stats([r.get('maxBehindTicks') for r in z]),'atBestReceiptFraction':stats([r.get('atBestReceiptFraction') for r in z]),'secondRestMs':stats([r.get('secondRestMs') for r in z])}
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--target-reference');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v23_qquality_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v23.load_runtime(a);rows=[];marketRows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=QueueProgressQualityShadowSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v23(models,cr['winner']);qq=sim.quality_rows()
   finally:sim.close()
   for q in qq:q['marketId']=mid
   rows.extend(qq);marketRows.append({'marketId':mid,'firstFills':r['reserveFirstLegActualFill'],'cycles':r['reserveCycleCompletion'],'ceilingSubmits':r['ceilingRepairSubmits'],'floorGain':r['reserveCycleFloorGainTotal'],'pnlDiagnosticOnly':r['pnlDiagnosticOnly'],'qualityOrders':len(qq)});print(json.dumps({'marketId':mid,'first':r['reserveFirstLegActualFill'],'cycles':r['reserveCycleCompletion'],'qOrders':len(qq),'filledQ':sum(x['filled'] for x in qq)},ensure_ascii=False),flush=True)
  out={'version':'ETH_V23_QUEUE_PROGRESS_QUALITY_SHADOW_V1','researchOnly':True,'behaviorChange':False,'selectedMarketIds':[x['marketId'] for x in marketRows],'marketRows':marketRows,'qualitySummary':summarize_quality(rows),'targetSuccessfulCheapPostfillReference':target_reference(a.target_reference),'rows':rows,'boundary':['V23 behavior copied exactly; audit records state only.','Public same-price depth is anonymous aggregate queue context, not private queue rank.','Depth decrease can be trades or cancels; gross re-add/churn is recorded to distinguish persistent depletion from transient book churn.','Target reference contains successful official Target second-leg episodes only and is descriptive teacher context, not a numeric threshold source.','No PnL tuning. Consumed-market research only.']}
  op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'qualitySummary':out['qualitySummary'],'targetReference':out['targetSuccessfulCheapPostfillReference']},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
