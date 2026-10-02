from __future__ import annotations
import argparse,json,lzma,tempfile,zipfile,shutil,statistics,sys,joblib
from pathlib import Path
from collections import deque
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as feed
EPS=1e-9;TTL=5000;MODES=('RAW_OBJECTIVE','HARD_PAIR_100','MODEL_ACCEPT')
def live(s):return s in {'NEW','PARTIALLY_FILLED'}
def fill_price(side,s,fallback):
 p=s.get('execPrice');
 if p is None:return float(fallback)
 p=float(p);return p if side=='UP' else 1-p
def apply(book,u):
 if int(u[3]):book['bids']={float(k):float(v) for k,v in (u[4] or {}).items()};book['asks']={float(k):float(v) for k,v in (u[5] or {}).items()};return
 for key in ('bids','asks'):
  for r in (u[6] or {}).get(key,[]) or []:
   p=float(r[0]);a=float(r[2]);
   if a<=EPS:book[key].pop(p,None)
   else:book[key][p]=a
def quotes(book):
 if not book['bids'] or not book['asks']:return None
 bb=max(book['bids']);ba=min(book['asks']);return {'UP':{'bid':float(bb),'ask':float(ba)},'DOWN':{'bid':1-float(ba),'ask':1-float(bb)}}
class Runner:
 def __init__(self,tape,traj,mode,accept=None):
  self.payload=json.loads(lzma.decompress(tape.read_bytes()).decode('utf-8'));feed.ARCHIVE_DIR=tape.parent;self.events,self.times,self.meta=feed.build_archive_events(int(self.payload['marketId']),trade_offset='mid');self.bt=ex.new_bt(self.events,entry_latency_ms=250,response_latency_ms=250,queue_model='risk');ex.initialize_bt(self.bt);self.traj=sorted(traj,key=lambda r:r['t']);self.mode=mode;self.accept=accept;self.ti=0;self.target={'UP':0.,'DOWN':0.};self.book={'bids':{},'asks':{}};self.orders={};self.n=1;self.inv={'UP':0.,'DOWN':0.};self.cost=0.;self.sideCost={'UP':0.,'DOWN':0.};self.unmatched={'UP':deque(),'DOWN':deque()};self.pairReserve=0.;self.pairedQty=0.;self.submits=0;self.fills=0;self.rejectedEconomic=0;self.fillHist=deque()
 def close(self):self.bt.close()
 def snap(self,o):return ex.order_snapshot(self.bt,o['n'])
 def record_fill(self,side,qty,px):
  self.inv[side]+=qty;self.cost+=qty*px;self.sideCost[side]+=qty*px;opp='DOWN' if side=='UP' else 'UP';left=qty
  while left>EPS and self.unmatched[opp]:
   oq,op=self.unmatched[opp][0];m=min(left,oq);self.pairReserve+=m*(1-px-op);self.pairedQty+=m;left-=m;oq-=m
   if oq<=EPS:self.unmatched[opp].popleft()
   else:self.unmatched[opp][0]=(oq,op)
  if left>EPS:self.unmatched[side].append((left,px))
 def process(self,t):
  for o in self.orders.values():
   s=self.snap(o);cum=float(s.get('cumExecQty') or 0);inc=max(0.,cum-o['cum'])
   if inc>EPS:
    px=fill_price(o['side'],s,o['price']);self.record_fill(o['side'],inc,px);self.fillHist.append((t,o['side'],inc,px));self.fills+=1;o['cum']=cum
   o['status']=s.get('status')
 def cancel_expired(self,t):
  for o in self.orders.values():
   s=self.snap(o)
   if live(s.get('status')) and t-o['placed']>=TTL:
    cur=self.bt.orders(0).get(o['n'])
    if cur is not None and bool(cur.cancellable):
     try:self.bt.cancel(0,o['n'],False)
     except Exception:pass
 def reserved(self,side):
  z=0.
  for o in self.orders.values():
   if o['side']==side and live(self.snap(o).get('status')):z+=max(0.,float(self.snap(o).get('leavesQty') or 0))
  return z
 def submit(self,side,p,q,t):
  n=self.n;self.n+=1;ex.submit_native(self.bt,n,side,p,q);self.orders[f'{side}_{n}']={'n':n,'side':side,'price':p,'qty':q,'cum':0.,'placed':t,'status':'NEW'};self.submits+=1
 def advance_target(self,t):
  while self.ti<len(self.traj) and int(self.traj[self.ti]['t'])<t:
   r=self.traj[self.ti];self.target[r['side']]+=float(r['shares']);self.ti+=1
 def unmatched_avg(self,side):
  xs=self.unmatched[side];q=sum(a for a,_ in xs);return sum(a*p for a,p in xs)/q if q>EPS else None
 def economic_ok(self,side,p,qty):
  if self.mode=='RAW_OBJECTIVE':return True
  opp='DOWN' if side=='UP' else 'UP';oppq=sum(a for a,_ in self.unmatched[opp]);sameq=sum(a for a,_ in self.unmatched[side]);target_abs=abs(self.target['UP']-self.target['DOWN']);max_unmatched=max(10.,target_abs)
  # Dominant/equal-side expansion is allowed only within Target-like surplus authority.
  if oppq<=EPS:
   return sameq+qty<=max_unmatched+1e-9
  pairqty=min(qty,oppq);avg=self.unmatched_avg(opp);pairsum=(avg+p) if avg is not None else 999.
  if self.mode=='HARD_PAIR_100':return pairsum<=1.0000001
  if self.mode=='MODEL_ACCEPT':
   return self.accept_ok(side,p,qty)
  debt=pairqty*max(0.,pairsum-1.);return debt<=max(0.,self.pairReserve)+1e-9
 def accept_ok(self,side,p,qty):
  if self.accept is None:return True
  t=self.now_t;u,d=self.inv['UP'],self.inv['DOWN'];gross=u+d;ab=abs(u-d);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
  if weak is None or side!=weak:return True
  while self.fillHist and t-self.fillHist[0][0]>30000:self.fillHist.popleft()
  def recent_avg(s):
   z=[x for x in self.fillHist if x[1]==s];q=sum(x[2] for x in z);return sum(x[2]*x[3] for x in z)/q if q>EPS else (self.sideCost[s]/self.inv[s] if self.inv[s]>EPS else 0.)
  qv=quotes(self.book);wb=qv[weak]['bid'];wa=qv[weak]['ask'];db=qv[dom]['bid'];da=qv[dom]['ask'];base=min(u,d);floor=base-self.cost;best=max(u,d)-self.cost;r5=[x for x in self.fillHist if t-x[0]<=5000];r15=[x for x in self.fillHist if t-x[0]<=15000];r10=[x for x in self.fillHist if t-x[0]<=10000];last=self.fillHist[-1] if self.fillHist else None
  f={'seconds_left':self.seconds_left,'abs_net':ab,'imbalance_ratio':ab/gross if gross else 0.,'pair_coverage':2*base/gross if gross else 0.,'floor':floor,'best_pnl':best,'base_pair_shares':base,'surplus_ratio':ab/gross if gross else 0.,'weak_gap':ab,'weak_bid':wb,'weak_ask':wa,'weak_spread':wa-wb,'dom_bid':db,'dom_ask':da,'marginal_pair_sum_weak':recent_avg(dom)+wa,'projected_floor_delta_weak_1':1-wa,'last_action_age_s':(t-last[0])/1000. if last else 999.,'maker_events_5s':float(len(r5)),'taker_events_5s':0.,'maker_events_15s':float(len(r15)),'taker_events_15s':0.,'maker_shares_10s':float(sum(x[2] for x in r10)),'taker_shares_10s':0.}
  import numpy as np
  X=np.asarray([[float(f.get(k,0.) or 0.) for k in self.accept['features']]],float);pr=float(self.accept['model'].predict_proba(X)[0,1]);return pr>=float(self.accept['threshold'])
 def run(self,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);ex.advance_to(self.bt,first)
  for u in ups:
   t=int(u[1]);self.now_t=t;ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);apply(self.book,u);self.advance_target(t);q=quotes(self.book)
   if not q:continue
   end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs']);
   self.seconds_left=(end-t)/1000.;
   if self.seconds_left<=180:continue
   # prioritize the larger responsibility deficit
   ds=[]
   for side in ('UP','DOWN'):
    d=max(0.,self.target[side]-self.inv[side]-self.reserved(side));ds.append((d,side))
   for deficit,side in sorted(ds,reverse=True):
    if deficit<=.25:continue
    p=float(q[side]['bid']);ask=float(q[side]['ask']);
    if p<=0 or p>=ask-EPS:continue
    qty=deficit;legal=1/p
    if qty<legal:
     if qty<.5*legal:continue
     qty=legal
    qty=min(qty,12.)
    if not self.economic_ok(side,p,qty):self.rejectedEconomic+=1;continue
    self.submit(side,p,qty,t)
  end=int(self.meta['lastReceivedMs']);ex.advance_to(self.bt,end);self.process(end);pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());pc=2*min(self.inv.values())/gross if gross>EPS else 0.;floor=min(self.inv.values())-self.cost
  return {'pnl':pnl,'buyNotional':self.cost,'up':self.inv['UP'],'down':self.inv['DOWN'],'pairCoverage':pc,'floor':floor,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'pairReserve':self.pairReserve,'pairedQty':self.pairedQty,'submits':self.submits,'fills':self.fills,'rejectedEconomic':self.rejectedEconomic,'targetUp':self.target['UP'],'targetDown':self.target['DOWN']}
def agg(rs):
 buy=sum(x['buyNotional'] for x in rs);p=sum(x['pnl'] for x in rs);a=[x for x in rs if x['buyNotional']>EPS]
 return {'markets':len(rs),'activeMarkets':len(a),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'activeWinRate':sum(x['pnl']>0 for x in a)/len(a) if a else None,'meanBuyActive':statistics.mean(x['buyNotional'] for x in a) if a else 0.,'meanPairCoverageActive':statistics.mean(x['pairCoverage'] for x in a) if a else 0.,'positiveFloorRateActive':sum(x['floor']>=0 for x in a)/len(a) if a else None,'meanFloorActive':statistics.mean(x['floor'] for x in a) if a else None,'meanAbsNetActive':statistics.mean(x['absNet'] for x in a) if a else 0.,'meanPairReserveActive':statistics.mean(x['pairReserve'] for x in a) if a else 0.,'meanSubmitsActive':statistics.mean(x['submits'] for x in a) if a else 0.}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--trajectory',required=True);ap.add_argument('--output',required=True);ap.add_argument('--accept-model',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_econoracle_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);accept=joblib.load(a.accept_model);cohort=[r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'] if r.get('cohortTag')=='DEV20'];tr=json.load(open(a.trajectory,encoding='utf-8'));rows=[];n=0
  for mode in MODES:
   for cr in cohort:
    n+=1;mid=int(cr['marketId']);r=Runner(tmp/'tapes'/f'{mid}.json.xz',tr.get(str(mid),[]),mode,accept)
    try:rr=r.run(cr['winner'])
    finally:r.close()
    rr.update({'marketId':mid,'mode':mode,'winner':cr['winner']});rows.append(rr)
   print(json.dumps({'modeDone':mode,'progress':n,'total':len(cohort)*len(MODES)}),flush=True)
  sums=[]
  for mode in MODES:
   s=agg([r for r in rows if r['mode']==mode]);s['mode']=mode;sums.append(s)
  out={'version':'ETH_MAKER_ECONOMIC_OBJECTIVE_ORACLE20_V1','boundary':['development diagnostic only; Target Maker inventory strict-past supplies responsibility direction/scale','all execution Maker-only best-bid post-only 5s TTL; <=180s no new exposure','HARD_PAIR_100 requires estimated unmatched pair sum<=1','MODEL_ACCEPT uses frozen economic-only weak-side placement acceptance teacher','dominant expansion limited to max(10 shares,current Target strict-past abs-net)'],'summaries':sums,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summaries':sums},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
