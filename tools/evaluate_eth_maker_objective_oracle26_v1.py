from __future__ import annotations
import argparse,json,lzma,tempfile,zipfile,shutil,statistics,sys
from pathlib import Path
from collections import deque
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as feed
EPS=1e-9;TTL=5000

def live(s):return s in {'NEW','PARTIALLY_FILLED'}
def fill_price(side,s,fallback):
 p=s.get('execPrice');
 if p is None:return float(fallback)
 p=float(p);return p if side=='UP' else 1-p

def apply(book,u):
 if int(u[3]):book['bids']={float(k):float(v) for k,v in (u[4] or {}).items()};book['asks']={float(k):float(v) for k,v in (u[5] or {}).items()};return
 ch=u[6] or {}
 for key in ('bids','asks'):
  for r in ch.get(key,[]) or []:
   p=float(r[0]);after=float(r[2])
   if after<=EPS:book[key].pop(p,None)
   else:book[key][p]=after

def quotes(book):
 if not book['bids'] or not book['asks']:return None
 bb=max(book['bids']);ba=min(book['asks']);return {'UP':{'bid':float(bb),'ask':float(ba)},'DOWN':{'bid':1-float(ba),'ask':1-float(bb)}}

class Runner:
 def __init__(self,tape,traj):
  self.payload=json.loads(lzma.decompress(tape.read_bytes()).decode('utf-8'));feed.ARCHIVE_DIR=tape.parent;self.events,self.times,self.meta=feed.build_archive_events(int(self.payload['marketId']),trade_offset='mid');self.bt=ex.new_bt(self.events,entry_latency_ms=250,response_latency_ms=250,queue_model='risk');ex.initialize_bt(self.bt);self.traj=sorted(traj,key=lambda r:r['t']);self.ti=0;self.target={'UP':0.,'DOWN':0.};self.book={'bids':{},'asks':{}};self.orders={};self.n=1;self.inv={'UP':0.,'DOWN':0.};self.cost=0.;self.submits=0;self.fills=0;self.makerFilled=0.;self.maxDeficit=0.;self.deficitSamples=[]
 def close(self):self.bt.close()
 def snap(self,o):return ex.order_snapshot(self.bt,o['n'])
 def process(self,t):
  for o in self.orders.values():
   s=self.snap(o);cum=float(s.get('cumExecQty') or 0);inc=max(0.,cum-o['cum'])
   if inc>EPS:
    px=fill_price(o['side'],s,o['price']);self.inv[o['side']]+=inc;self.cost+=inc*px;self.fills+=1;self.makerFilled+=inc;o['cum']=cum
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
   if o['side']!=side:continue
   s=self.snap(o)
   if live(s.get('status')):z+=max(0.,float(s.get('leavesQty') or 0))
  return z
 def submit(self,side,price,qty,t):
  if qty<=EPS:return False
  num=self.n;self.n+=1;rc=ex.submit_native(self.bt,num,side,float(price),float(qty));self.orders[f'{side}_{num}']={'n':num,'side':side,'price':float(price),'qty':float(qty),'cum':0.,'placed':t,'status':'NEW'};self.submits+=1;return True
 def advance_target(self,t):
  while self.ti<len(self.traj) and int(self.traj[self.ti]['t'])<t:
   r=self.traj[self.ti];self.target[str(r['side'])]+=float(r['shares']);self.ti+=1
 def run(self,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);ex.advance_to(self.bt,first)
  for u in ups:
   t=int(u[1]);ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);apply(self.book,u);self.advance_target(t);q=quotes(self.book)
   if not q:continue
   end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs']);
   if (end-t)/1000.<=180:continue
   for side in ('UP','DOWN'):
    deficit=max(0.,self.target[side]-self.inv[side]-self.reserved(side));self.maxDeficit=max(self.maxDeficit,deficit);self.deficitSamples.append(deficit)
    if deficit<=.25:continue
    p=float(q[side]['bid']);ask=float(q[side]['ask'])
    if p<=0 or p>=ask-EPS:continue
    legal=1.0/p;qty=deficit
    if qty+1e-9<legal:
     # only overshoot if deficit is at least half of the legal minimum
     if qty<0.5*legal:continue
     qty=legal
    qty=min(qty,12.0)
    self.submit(side,p,qty,t)
  end=int(self.meta['lastReceivedMs']);ex.advance_to(self.bt,end);self.process(end);pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=self.inv['UP']+self.inv['DOWN'];pc=2*min(self.inv.values())/gross if gross>EPS else 0.;floor=min(self.inv.values())-self.cost;terr=abs(self.target['UP']-self.inv['UP'])+abs(self.target['DOWN']-self.inv['DOWN'])
  return {'pnl':pnl,'buyNotional':self.cost,'up':self.inv['UP'],'down':self.inv['DOWN'],'targetUpMaker':self.target['UP'],'targetDownMaker':self.target['DOWN'],'terminalTargetL1Error':terr,'pairCoverage':pc,'floor':floor,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fillEvents':self.fills,'makerFilled':self.makerFilled,'maxDeficit':self.maxDeficit,'meanDeficit':statistics.mean(self.deficitSamples) if self.deficitSamples else 0.}

def agg(rs):
 buy=sum(r['buyNotional'] for r in rs);p=sum(r['pnl'] for r in rs);act=[r for r in rs if r['buyNotional']>EPS]
 return {'markets':len(rs),'activeMarkets':len(act),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'activeWinRate':sum(r['pnl']>0 for r in act)/len(act) if act else None,'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanMakerFilled':statistics.mean(r['makerFilled'] for r in rs),'meanTargetL1Error':statistics.mean(r['terminalTargetL1Error'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--trajectory',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_objoracle_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];tr=json.load(open(a.trajectory,encoding='utf-8'));rows=[]
  for i,cr in enumerate(cohort,1):
   mid=int(cr['marketId']);r=Runner(tmp/'tapes'/f'{mid}.json.xz',tr.get(str(mid),[]))
   try:rr=r.run(cr['winner'])
   finally:r.close()
   rr.update({'marketId':mid,'cohortTag':cr.get('cohortTag'),'winner':cr['winner'],'targetPnl':cr.get('targetPnl'),'targetBuy':cr.get('targetBuy')});rows.append(rr)
   if i%5==0:print(json.dumps({'progress':i,'total':len(cohort),'marketId':mid}),flush=True)
  sums=[]
  for tag in ('DEV20','FRESH6','ALL'):
   rs=[r for r in rows if tag=='ALL' or r['cohortTag']==tag];s=agg(rs);s['cohortTag']=tag;sums.append(s)
  out={'version':'ETH_MAKER_OBJECTIVE_ORACLE26_V1','boundary':['development diagnostic only','oracle may see Target Maker acquisitions strictly before each receipt time; no future Target actions/outcomes','our execution is Maker-only best-bid post-only, 5s TTL, 1 USDT minimum, <=180s no new exposure','tests whether knowing contemporaneous Target Maker portfolio objective is sufficient to repair our own HFT inventory'],'summaries':sums,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summaries':sums},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
