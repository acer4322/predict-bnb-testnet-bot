from __future__ import annotations
import argparse, bisect, json, lzma, math, statistics, tempfile, zipfile, shutil, sys
from collections import deque
from pathlib import Path
import joblib, numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as feed

POLL_MS=250; DECISION_MS=1000; MAKER_TTL_MS=5000; EPS=1e-9

def live(s): return s in {'NEW','PARTIALLY_FILLED'}
def book(bt):
 d=bt.depth(0); a=d.snapshot()
 try:
  bids={};asks={}
  for r in a:
   q=float(r['qty']);p=round(float(r['px']),12);ev=int(r['ev'])
   if q<=EPS: continue
   if ev & int(ex.BUY_EVENT): bids[p]=q
   elif ev & int(ex.SELL_EVENT): asks[p]=q
  return bids,asks
 finally:d.snapshot_free(a)
def topn(d,reverse,n=3): return float(sum(d[k] for k in sorted(d,reverse=reverse)[:n])) if d else 0.
def bf_from_bt(bt,order_count):
 bids,asks=book(bt)
 if not bids or not asks:return None
 bb=max(bids);ba=min(asks);ub=float(bb);ua=float(ba);db=1-ua;da=1-ub;tb=topn(bids,True);ta=topn(asks,False)
 return {'up_bid':ub,'up_ask':ua,'up_mid':(ub+ua)/2,'down_bid':db,'down_ask':da,'down_mid':(db+da)/2,'up_spread':ua-ub,'down_spread':da-db,'up_bid_depth':float(bids[bb]),'up_ask_depth':float(asks[ba]),'up_top3_bid_depth':tb,'up_top3_ask_depth':ta,'book_order_count':float(order_count),'book_depth_imbalance':(tb-ta)/(tb+ta) if tb+ta>EPS else 0.}
def state_features(inv,cost,hist,t,events_seen,bf):
 up,down=inv['UP'],inv['DOWN'];gross=up+down;net=up-down;ab=abs(net);base=min(up,down);floor=base-cost;best=max(up,down)-cost
 su=sum(x[3] for x in hist if x[2]=='UP');sd=sum(x[3] for x in hist if x[2]=='DOWN');avg_up=sum(x[4]*x[3] for x in hist if x[2]=='UP')/su if su>EPS else 0.;avg_dn=sum(x[4]*x[3] for x in hist if x[2]=='DOWN')/sd if sd>EPS else 0.
 weak='UP' if up<down-EPS else 'DOWN' if down<up-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
 def vals(side):
  if side=='UP':return bf['up_bid'],bf['up_ask'],bf['up_mid'],bf['up_spread']
  if side=='DOWN':return bf['down_bid'],bf['down_ask'],bf['down_mid'],bf['down_spread']
  return 0.,0.,0.,0.
 wb,wa,wm,ws=vals(weak);db,da,dm,_=vals(dom);avg_dom=avg_dn if dom=='DOWN' else avg_up if dom=='UP' else 0.;last=hist[-1] if hist else None;r5=[x for x in hist if t-x[0]<=5000];r15=[x for x in hist if t-x[0]<=15000];r10=[x for x in hist if t-x[0]<=10000]
 f=dict(bf);f.update({'gross_shares':gross,'net_shares':net,'abs_net':ab,'imbalance_ratio':ab/gross if gross else 0.,'pair_coverage':2*base/gross if gross else 0.,'cost':cost,'floor':floor,'best_pnl':best,'base_pair_shares':base,'avg_cost_up':avg_up,'avg_cost_down':avg_dn,'surplus_ratio':ab/gross if gross else 0.,'weak_side_up':1. if weak=='UP' else -1. if weak=='DOWN' else 0.,'weak_gap':ab,'weak_bid':wb,'weak_ask':wa,'weak_mid':wm,'weak_spread':ws,'dom_bid':db,'dom_ask':da,'dom_mid':dm,'marginal_pair_sum_weak':avg_dom+wa if weak else 0.,'projected_floor_delta_weak_1':1-wa if weak else 0.,'projected_floor_delta_dom_1':-da if dom else 0.,'last_action_age_s':(t-last[0])/1000 if last else 999.,'last_role_taker':1. if last and last[1]=='TAKER' else 0.,'last_side_up':1. if last and last[2]=='UP' else 0.,'last_price':last[4] if last else 0.,'last_shares':last[3] if last else 0.,'maker_events_5s':float(sum(x[1]=='MAKER' for x in r5)),'taker_events_5s':float(sum(x[1]=='TAKER' for x in r5)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'maker_shares_10s':float(sum(x[3] for x in r10 if x[1]=='MAKER')),'taker_shares_10s':float(sum(x[3] for x in r10 if x[1]=='TAKER')),'events_seen_scaled':min(1.,events_seen/50.)})
 return f,weak,dom

def load_order_counts(path):
 j=json.loads(lzma.open(path,'rt',encoding='utf-8').read()); rows=[(int(r[1]),int(r[2] or 0)) for r in j.get('updates',[])]; rows.sort();return [x[0] for x in rows],[x[1] for x in rows]
def asof_count(times,vals,t):
 i=bisect.bisect_right(times,t)-1;return vals[i] if i>=0 else 0

class Runner:
 def __init__(self,tape,model_path,entry,response,queue):
  self.bundle=joblib.load(model_path);self.features=self.bundle['features'];self.models=self.bundle['models'];self.th=self.bundle['thresholds'];self.ot,self.ov=load_order_counts(tape)
  feed.ARCHIVE_DIR=tape.parent;self.events,self.times,self.meta=feed.build_archive_events(int(tape.stem.split('.')[0]),trade_offset='mid');self.bt=ex.new_bt(self.events,entry_latency_ms=entry,response_latency_ms=response,queue_model=queue);ex.initialize_bt(self.bt)
  self.inv={'UP':0.,'DOWN':0.};self.cost=0.;self.hist=deque();self.orders={};self.next=1;self.eventsSeen=0;self.fills=0;self.makerFilled=0.;self.takerFilled=0.;self.domFilled=0.;self.weakFilled=0.;self.trace=[]
 def close(self):self.bt.close()
 def snap(self,o):return ex.order_snapshot(self.bt,o['num'])
 def process(self,now):
  for k,o in list(self.orders.items()):
   s=self.snap(o);cum=float(s.get('cumExecQty') or 0);inc=max(0.,cum-o['cum'])
   if inc>EPS:
    ep=s.get('execPrice');px=float(ep) if ep is not None else o['price'];px=px if o['side']=='UP' else 1-px
    preweak='UP' if self.inv['UP']<self.inv['DOWN']-EPS else 'DOWN' if self.inv['DOWN']<self.inv['UP']-EPS else None
    self.inv[o['side']]+=inc;self.cost+=inc*px;self.hist.append((now,o['role'],o['side'],inc,px));self.eventsSeen+=1;self.fills+=1;o['cum']=cum
    if o['role']=='MAKER':self.makerFilled+=inc
    else:self.takerFilled+=inc
    if preweak and o['side']==preweak:self.weakFilled+=inc
    elif preweak:self.domFilled+=inc
    while self.hist and now-self.hist[0][0]>30000:self.hist.popleft()
    self.trace.append({'atMs':now,'type':'FILL','role':o['role'],'side':o['side'],'qty':inc,'px':px})
   o['status']=s.get('status')
 def cancel(self,k,now):
  o=self.orders.get(k)
  if not o:return
  s=self.snap(o)
  if live(s.get('status')):
   cur=self.bt.orders(0).get(o['num'])
   if cur is not None and bool(cur.cancellable):
    try:self.bt.cancel(0,o['num'],False);o['cancelMs']=now
    except Exception:pass
 def submit_maker(self,side,price,qty,now):
  # one reactive resting carrier; replacement only after cancel/terminal
  for k,o in list(self.orders.items()):
   if o['role']=='MAKER' and live(self.snap(o).get('status')):
    if o['side']==side and abs(o['price']-price)<=0.005:return False
    self.cancel(k,now);return False
  qty=max(float(qty),1.0/max(price,0.01));qty=min(qty,40.0)
  if price<0.10 or qty*price<1-EPS:return False
  num=self.next;self.next+=1;rc=ex.submit_native(self.bt,num,side,price,qty);k=f'M{num}';self.orders[k]={'num':num,'role':'MAKER','side':side,'price':price,'qty':qty,'cum':0.,'placed':now,'status':'NEW'};self.trace.append({'atMs':now,'type':'SUBMIT','role':'MAKER','side':side,'px':price,'qty':qty,'rc':rc});return True
 def submit_taker(self,side,ask,qty,now):
  qty=max(1.0/0.95,float(qty));qty=min(qty,80.0);maxp=max(ask,min(.95,1.0/qty+0.01))
  num=self.next;self.next+=1;ns,np_=ex.native_order(side,maxp)
  if ns=='BUY':rc=int(self.bt.submit_buy_order(0,num,np_,qty,ex.hbt.GTC,ex.LIMIT,False))
  else:rc=int(self.bt.submit_sell_order(0,num,np_,qty,ex.hbt.GTC,ex.LIMIT,False))
  k=f'T{num}';self.orders[k]={'num':num,'role':'TAKER','side':side,'price':maxp,'qty':qty,'cum':0.,'placed':now,'status':'NEW'};self.trace.append({'atMs':now,'type':'SUBMIT','role':'TAKER','side':side,'ask':ask,'maxp':maxp,'qty':qty,'rc':rc});return True
 def run(self,winner):
  first=int(self.meta['firstReceivedMs']);last=int(self.meta['lastReceivedMs']);now=first+2000;ex.advance_to(self.bt,now);self.process(now);nextd=now
  while now<last:
   now=min(last,now+POLL_MS);ex.advance_to(self.bt,now);self.process(now)
   # stale maker carrier TTL
   for k,o in list(self.orders.items()):
    if o['role']=='MAKER' and live(self.snap(o).get('status')) and now-o['placed']>=MAKER_TTL_MS:self.cancel(k,now)
   if now<nextd:continue
   nextd=now+DECISION_MS;oc=asof_count(self.ot,self.ov,now);bf=bf_from_bt(self.bt,oc)
   if not bf:continue
   bf['seconds_left']=max(0.,(last-now)/1000.);f,weak,dom=state_features(self.inv,self.cost,self.hist,now,self.eventsSeen,bf);x=np.asarray([[float(f.get(k,0.) or 0.) for k in self.features]],float)
   pa=float(self.models['action'].predict_proba(x)[0,1])
   if pa<self.th['action']:continue
   pt=float(self.models['taker'].predict_proba(x)[0,1]);role='TAKER' if pt>=self.th['takerGivenAction'] else 'MAKER';pw=float(self.models['weak'].predict_proba(x)[0,1]);ps=float(self.models['side_up'].predict_proba(x)[0,1])
   if weak:
    side=weak if pw>=self.th['weakGivenAction'] else dom
   else:side='UP' if ps>=self.th['sideUpGivenAction'] else 'DOWN'
   # fixed project safety: after 180s only weak-side repair may continue.
   if bf['seconds_left']<=180 and (weak is None or side!=weak):continue
   qm=self.models['qty_taker'] if role=='TAKER' else self.models['qty_maker'];qty=float(np.expm1(np.clip(qm.predict(x)[0],0,10)));qty=max(1.,min(80. if role=='TAKER' else 20.,qty))
   if role=='MAKER':self.submit_maker(side,bf['up_bid'] if side=='UP' else bf['down_bid'],qty,now)
   else:self.submit_taker(side,bf['up_ask'] if side=='UP' else bf['down_ask'],qty,now)
  for k in list(self.orders):self.cancel(k,last)
  ex.advance_to(self.bt,last);self.process(last);pnl=self.inv[winner]-self.cost
  g=self.inv['UP']+self.inv['DOWN'];pc=2*min(self.inv.values())/g if g>EPS else 0.;return {'pnl':pnl,'buyNotional':self.cost,'up':self.inv['UP'],'down':self.inv['DOWN'],'pairCoverage':pc,'floor':min(self.inv.values())-self.cost,'fills':self.fills,'makerFilled':self.makerFilled,'takerFilled':self.takerFilled,'weakFilled':self.weakFilled,'dominantFilled':self.domFilled,'traceEvents':len(self.trace)}
def agg(rows):
 ps=[r['pnl'] for r in rows];buy=sum(r['buyNotional'] for r in rows);return {'markets':len(rows),'pnl':sum(ps),'buyNotional':buy,'roi':sum(ps)/buy if buy else None,'winRate':sum(x>0 for x in ps)/len(ps) if ps else None,'meanPnl':statistics.mean(ps),'medianPnl':statistics.median(ps),'maxWin':max(ps),'maxLoss':min(ps),'meanBuy':statistics.mean(r['buyNotional'] for r in rows),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rows),'positiveFloorRate':sum(r['floor']>=0 for r in rows)/len(rows),'meanAbsNet':statistics.mean(abs(r['up']-r['down']) for r in rows),'meanMakerFilled':statistics.mean(r['makerFilled'] for r in rows),'meanTakerFilled':statistics.mean(r['takerFilled'] for r in rows),'weakShareOfDirectionalFills':sum(r['weakFilled'] for r in rows)/(sum(r['weakFilled']+r['dominantFilled'] for r in rows) or 1.)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_teacher_hft_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.loads((tmp/'cohort.json').read_text())['rows'];settings=[(250,250,'risk'),(750,250,'risk'),(250,250,'log')];rows=[];n=0
  for entry,response,queue in settings:
   for c in co:
    n+=1;r=Runner(tmp/'tapes'/f"{int(c['marketId'])}.json.xz",Path(a.model),entry,response,queue)
    try:o=r.run(str(c['winner']).upper())
    finally:r.close()
    o.update({'marketId':int(c['marketId']),'winner':c['winner'],'targetPnl':c['targetPnl'],'targetBuy':c['targetBuy'],'entryLatencyMs':entry,'responseLatencyMs':response,'queueModel':queue});rows.append(o)
    if n%10==0:print(json.dumps({'progress':n,'total':len(co)*len(settings)}),flush=True)
  sums=[]
  for e,r,q in settings:
   arows=[x for x in rows if x['entryLatencyMs']==e and x['responseLatencyMs']==r and x['queueModel']==q];s=agg(arows);s.update({'entryLatencyMs':e,'responseLatencyMs':r,'queueModel':q});sums.append(s)
  target={'markets':len(co),'pnl':sum(float(x['targetPnl']) for x in co),'buyNotional':sum(float(x['targetBuy']) for x in co)};target['roi']=target['pnl']/target['buyNotional'];target['winRate']=sum(float(x['targetPnl'])>0 for x in co)/len(co)
  out={'version':'ETH_TARGET_TEACHER_POLICY_HFT_DEV20_V1','boundary':['dev20 was excluded from model training/validation/test but was previously used for structural manual development; diagnostic, not fresh formal holdout','decisions use only OUR closed-loop inventory/fill history and contemporaneous ETH public book/orderCount','Target action/side/qty/winner never enter decisions; winner only settlement scoring','teacher labels are first observed acquisition events, not clean placement labels; Maker uses reactive 5s resting carrier approximation','after <=180s only weak-side repair may continue'],'targetSameCohort':target,'summaries':sums,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'target':target,'summaries':sums},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
