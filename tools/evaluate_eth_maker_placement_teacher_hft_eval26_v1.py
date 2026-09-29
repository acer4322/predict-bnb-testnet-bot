from __future__ import annotations
import argparse,bisect,json,lzma,math,statistics,tempfile,zipfile,shutil,sys
from collections import deque
from pathlib import Path
import joblib,numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as feed
POLL_MS=250;DECISION_MS=1000;EPS=1e-9
VARIANTS={'DIRECT_TTL5':{'selector':'direct','ttl':5000},'DIRECT_TTL12':{'selector':'direct','ttl':12000},'WEAK_TTL12':{'selector':'weak','ttl':12000}}
PENDING=['pending_up_qty','pending_down_qty','pending_gross_qty','pending_net_qty','pending_abs_net_qty','pending_count','pending_up_count','pending_down_count','pending_oldest_age_s','pending_newest_age_s','pending_up_at_best_qty','pending_down_at_best_qty','pending_up_mean_offset_ticks','pending_down_mean_offset_ticks','effective_up_qty','effective_down_qty','effective_abs_net','effective_pair_coverage','effective_weak_side_up','effective_weak_gap','pending_materialized_weak_qty','pending_materialized_dom_qty','up_inside_empty_ticks','down_inside_empty_ticks','up_bid_depth_m1','up_bid_depth_m2','up_bid_depth_m3','down_bid_depth_m1','down_bid_depth_m2','down_bid_depth_m3']
def live(s):return s in {'NEW','PARTIALLY_FILLED'}
def snapbook(bt):
 d=bt.depth(0);a=d.snapshot()
 try:
  bids={};asks={}
  for r in a:
   q=float(r['qty']);p=round(float(r['px']),12);ev=int(r['ev'])
   if q<=EPS:continue
   if ev&int(ex.BUY_EVENT):bids[p]=q
   elif ev&int(ex.SELL_EVENT):asks[p]=q
  return bids,asks
 finally:d.snapshot_free(a)
def topn(d,reverse,n=3):return float(sum(d[k] for k in sorted(d,reverse=reverse)[:n])) if d else 0.
def book_features(bt,oc):
 bids,asks=snapbook(bt)
 if not bids or not asks:return None,None
 bb=max(bids);ba=min(asks);ub=float(bb);ua=float(ba);db=1-ua;da=1-ub;tb=topn(bids,True);ta=topn(asks,False)
 f={'up_bid':ub,'up_ask':ua,'up_mid':(ub+ua)/2,'down_bid':db,'down_ask':da,'down_mid':(db+da)/2,'up_spread':ua-ub,'down_spread':da-db,'up_bid_depth':float(bids[bb]),'up_ask_depth':float(asks[ba]),'up_top3_bid_depth':tb,'up_top3_ask_depth':ta,'book_order_count':float(oc),'book_depth_imbalance':(tb-ta)/(tb+ta) if tb+ta>EPS else 0.}
 return f,{'bids':bids,'asks':asks}
def base_state(inv,cost,hist,t,events_seen,bf):
 up,down=inv['UP'],inv['DOWN'];gross=up+down;net=up-down;ab=abs(net);base=min(up,down);floor=base-cost;best=max(up,down)-cost
 su=sum(x[3] for x in hist if x[2]=='UP');sd=sum(x[3] for x in hist if x[2]=='DOWN');avg_up=sum(x[4]*x[3] for x in hist if x[2]=='UP')/su if su>EPS else 0.;avg_dn=sum(x[4]*x[3] for x in hist if x[2]=='DOWN')/sd if sd>EPS else 0.
 weak='UP' if up<down-EPS else 'DOWN' if down<up-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
 def vals(s):
  if s=='UP':return bf['up_bid'],bf['up_ask'],bf['up_mid'],bf['up_spread']
  if s=='DOWN':return bf['down_bid'],bf['down_ask'],bf['down_mid'],bf['down_spread']
  return 0.,0.,0.,0.
 wb,wa,wm,ws=vals(weak);db,da,dm,_=vals(dom);avgdom=avg_dn if dom=='DOWN' else avg_up if dom=='UP' else 0.;last=hist[-1] if hist else None;r5=[x for x in hist if t-x[0]<=5000];r15=[x for x in hist if t-x[0]<=15000];r10=[x for x in hist if t-x[0]<=10000]
 f=dict(bf);f.update({'gross_shares':gross,'net_shares':net,'abs_net':ab,'imbalance_ratio':ab/gross if gross else 0.,'pair_coverage':2*base/gross if gross else 0.,'cost':cost,'floor':floor,'best_pnl':best,'base_pair_shares':base,'avg_cost_up':avg_up,'avg_cost_down':avg_dn,'surplus_ratio':ab/gross if gross else 0.,'weak_side_up':1. if weak=='UP' else -1. if weak=='DOWN' else 0.,'weak_gap':ab,'weak_bid':wb,'weak_ask':wa,'weak_mid':wm,'weak_spread':ws,'dom_bid':db,'dom_ask':da,'dom_mid':dm,'marginal_pair_sum_weak':avgdom+wa if weak else 0.,'projected_floor_delta_weak_1':1-wa if weak else 0.,'projected_floor_delta_dom_1':-da if dom else 0.,'last_action_age_s':(t-last[0])/1000 if last else 999.,'last_role_taker':0.,'last_side_up':1. if last and last[2]=='UP' else 0.,'last_price':last[4] if last else 0.,'last_shares':last[3] if last else 0.,'maker_events_5s':float(len(r5)),'taker_events_5s':0.,'maker_events_15s':float(len(r15)),'taker_events_15s':0.,'maker_shares_10s':float(sum(x[3] for x in r10)),'taker_shares_10s':0.,'events_seen_scaled':min(1.,events_seen/50.)})
 return f,weak,dom
def side_depth(book,side,p):
 p=round(float(p),2)
 return float(book['bids'].get(p,0.)) if side=='UP' else float(book['asks'].get(round(1-p,2),0.))
def pending_state(active,inv,bf,book,t):
 pu=sum(o['remaining'] for o in active if o['side']=='UP');pd=sum(o['remaining'] for o in active if o['side']=='DOWN');pg=pu+pd;pn=pu-pd;ages=[(t-o['placed'])/1000 for o in active];uoffs=[(o['price']-bf['up_bid'])/.01 for o in active if o['side']=='UP'];doffs=[(o['price']-bf['down_bid'])/.01 for o in active if o['side']=='DOWN'];pub=sum(o['remaining'] for o in active if o['side']=='UP' and abs(o['price']-bf['up_bid'])<=.005);pdb=sum(o['remaining'] for o in active if o['side']=='DOWN' and abs(o['price']-bf['down_bid'])<=.005)
 eu=inv['UP']+pu;ed=inv['DOWN']+pd;eg=eu+ed;eab=abs(eu-ed);ew='UP' if eu<ed-EPS else 'DOWN' if ed<eu-EPS else None;mw='UP' if inv['UP']<inv['DOWN']-EPS else 'DOWN' if inv['DOWN']<inv['UP']-EPS else None;md='DOWN' if mw=='UP' else 'UP' if mw=='DOWN' else None
 f={'pending_up_qty':pu,'pending_down_qty':pd,'pending_gross_qty':pg,'pending_net_qty':pn,'pending_abs_net_qty':abs(pn),'pending_count':float(len(active)),'pending_up_count':float(sum(o['side']=='UP' for o in active)),'pending_down_count':float(sum(o['side']=='DOWN' for o in active)),'pending_oldest_age_s':max(ages) if ages else 0.,'pending_newest_age_s':min(ages) if ages else 0.,'pending_up_at_best_qty':pub,'pending_down_at_best_qty':pdb,'pending_up_mean_offset_ticks':float(np.mean(uoffs)) if uoffs else 0.,'pending_down_mean_offset_ticks':float(np.mean(doffs)) if doffs else 0.,'effective_up_qty':eu,'effective_down_qty':ed,'effective_abs_net':eab,'effective_pair_coverage':2*min(eu,ed)/eg if eg>EPS else 0.,'effective_weak_side_up':1. if ew=='UP' else -1. if ew=='DOWN' else 0.,'effective_weak_gap':eab,'pending_materialized_weak_qty':sum(o['remaining'] for o in active if mw and o['side']==mw),'pending_materialized_dom_qty':sum(o['remaining'] for o in active if md and o['side']==md),'up_inside_empty_ticks':max(0.,(bf['up_ask']-bf['up_bid'])/.01-1),'down_inside_empty_ticks':max(0.,(bf['down_ask']-bf['down_bid'])/.01-1)}
 for s,prefix,bid in [('UP','up',bf['up_bid']),('DOWN','down',bf['down_bid'])]:
  for k in (1,2,3):f[f'{prefix}_bid_depth_m{k}']=side_depth(book,s,bid-.01*k)
 return f
def load_counts(path):
 j=json.loads(lzma.open(path,'rt',encoding='utf-8').read());r=sorted((int(x[1]),int(x[2] or 0)) for x in j.get('updates',[]));return [x[0] for x in r],[x[1] for x in r]
def asof(t,v,x):i=bisect.bisect_right(t,x)-1;return v[i] if i>=0 else 0
class Runner:
 def __init__(self,tape,model,variant):
  self.m=joblib.load(model);self.features=self.m['features'];self.th=self.m['thresholds'];self.v=VARIANTS[variant];self.ot,self.ov=load_counts(tape);feed.ARCHIVE_DIR=tape.parent;self.events,self.times,self.meta=feed.build_archive_events(int(tape.stem.split('.')[0]),trade_offset='mid');self.bt=ex.new_bt(self.events,entry_latency_ms=750,response_latency_ms=250,queue_model='risk');ex.initialize_bt(self.bt);self.inv={'UP':0.,'DOWN':0.};self.cost=0.;self.hist=deque();self.orders={};self.next=1;self.eventsSeen=0;self.makerFilled=0.;self.weakFilled=0.;self.domFilled=0.;self.submitCount=0;self.offsetPred=[];self.qtyPred=[]
 def close(self):self.bt.close()
 def osnap(self,o):return ex.order_snapshot(self.bt,o['num'])
 def process(self,now):
  for o in self.orders.values():
   s=self.osnap(o);cum=float(s.get('cumExecQty') or 0);inc=max(0.,cum-o['cum'])
   if inc>EPS:
    ep=s.get('execPrice');px=float(ep) if ep is not None else o['native_price'];px=px if o['side']=='UP' else 1-px;preweak='UP' if self.inv['UP']<self.inv['DOWN']-EPS else 'DOWN' if self.inv['DOWN']<self.inv['UP']-EPS else None;self.inv[o['side']]+=inc;self.cost+=inc*px;self.hist.append((now,'MAKER',o['side'],inc,px));self.eventsSeen+=1;self.makerFilled+=inc;o['cum']=cum
    if preweak and o['side']==preweak:self.weakFilled+=inc
    elif preweak:self.domFilled+=inc
    while self.hist and now-self.hist[0][0]>30000:self.hist.popleft()
   o['status']=s.get('status');o['remaining']=max(0.,o['qty']-cum)
 def cancel(self,o):
  s=self.osnap(o)
  if live(s.get('status')):
   cur=self.bt.orders(0).get(o['num'])
   if cur is not None and bool(cur.cancellable):
    try:self.bt.cancel(0,o['num'],False)
    except Exception:pass
 def active(self):return [o for o in self.orders.values() if live(self.osnap(o).get('status')) and o['remaining']>EPS]
 def submit(self,side,price,qty,now):
  act=self.active()
  if len(act)>=8:return False
  if any(o['side']==side and abs(o['price']-price)<=.005 for o in act):return False
  price=round(float(price),2);qty=max(float(qty),1./max(price,.01));qty=min(qty,20.)
  if price<.10 or price>=1 or price*qty<1-EPS:return False
  num=self.next;self.next+=1;ns,np_=ex.native_order(side,price);rc=ex.submit_native(self.bt,num,side,price,qty);self.orders[num]={'num':num,'side':side,'price':price,'native_price':np_,'qty':qty,'remaining':qty,'cum':0.,'placed':now,'status':'NEW','rc':rc};self.submitCount+=1;return True
 def run(self,winner):
  first=int(self.meta['firstReceivedMs']);last=int(self.meta['lastReceivedMs']);now=first+2000;ex.advance_to(self.bt,now);self.process(now);nextd=now
  while now<last:
   now=min(last,now+POLL_MS);ex.advance_to(self.bt,now);self.process(now)
   for o in self.active():
    if now-o['placed']>=self.v['ttl']:self.cancel(o)
   if now<nextd:continue
   nextd=now+DECISION_MS;bf,bk=book_features(self.bt,asof(self.ot,self.ov,now))
   if not bf:continue
   sec=max(0.,(last-now)/1000.);bf['seconds_left']=sec
   if sec<=180:continue
   f,weak,dom=base_state(self.inv,self.cost,self.hist,now,self.eventsSeen,bf);act=self.active();f.update(pending_state(act,self.inv,bf,bk,now));x=np.asarray([[float(f.get(k,0.) or 0.) for k in self.features]],float);ph=float(self.m['hazardModel'].predict_proba(x)[0,1])
   if ph<self.th['placementHazard']:continue
   ps=float(self.m['sideModel'].predict_proba(x)[0,1]);pw=float(self.m['weakModel'].predict_proba(x)[0,1])
   if self.v['selector']=='weak' and weak:side=weak if pw>=self.th['weak'] else dom
   else:side='UP' if ps>=self.th['sideUp'] else 'DOWN'
   off=float(self.m['offsetModel'].predict(x)[0]);self.offsetPred.append(off);offi=int(round(max(-5,min(5,off))));bid=bf['up_bid'] if side=='UP' else bf['down_bid'];ask=bf['up_ask'] if side=='UP' else bf['down_ask'];price=round(bid+.01*offi,2);price=min(price,round(ask-.01,2));price=max(.10,price);qty=float(np.expm1(np.clip(self.m['qtyModel'].predict(x)[0],0,8)));self.qtyPred.append(qty);self.submit(side,price,qty,now)
  for o in self.active():self.cancel(o)
  ex.advance_to(self.bt,last);self.process(last);pnl=self.inv[winner]-self.cost;g=self.inv['UP']+self.inv['DOWN'];return {'pnl':pnl,'buyNotional':self.cost,'up':self.inv['UP'],'down':self.inv['DOWN'],'pairCoverage':2*min(self.inv.values())/g if g>EPS else 0.,'floor':min(self.inv.values())-self.cost,'makerFilled':self.makerFilled,'weakFilled':self.weakFilled,'dominantFilled':self.domFilled,'submits':self.submitCount,'meanOffsetPred':statistics.mean(self.offsetPred) if self.offsetPred else None,'meanQtyPred':statistics.mean(self.qtyPred) if self.qtyPred else None}
def agg(rows):
 ps=[r['pnl'] for r in rows];buy=sum(r['buyNotional'] for r in rows);active=[r for r in rows if r['buyNotional']>EPS];return {'markets':len(rows),'activeMarkets':len(active),'pnl':sum(ps),'buyNotional':buy,'roi':sum(ps)/buy if buy else None,'winRateAll':sum(x>0 for x in ps)/len(ps),'winRateActive':sum(r['pnl']>0 for r in active)/len(active) if active else None,'meanBuy':statistics.mean(r['buyNotional'] for r in rows),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rows),'positiveFloorRate':sum(r['floor']>=0 for r in rows)/len(rows),'meanAbsNet':statistics.mean(abs(r['up']-r['down']) for r in rows),'meanMakerFilled':statistics.mean(r['makerFilled'] for r in rows),'weakShare':sum(r['weakFilled'] for r in rows)/(sum(r['weakFilled']+r['dominantFilled'] for r in rows) or 1.),'maxWin':max(ps),'maxLoss':min(ps)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_maker_place_eval_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.loads((tmp/'cohort.json').read_text())['rows'];rows=[];total=len(co)*len(VARIANTS);n=0
  for vn in VARIANTS:
   for c in co:
    n+=1;r=Runner(tmp/'tapes'/f"{int(c['marketId'])}.json.xz",Path(a.model),vn)
    try:o=r.run(str(c['winner']).upper())
    finally:r.close()
    o.update({'marketId':int(c['marketId']),'evalSplit':c.get('evalSplit'),'variant':vn,'winner':c['winner'],'targetPnl':c['targetPnl'],'targetBuy':c['targetBuy']});rows.append(o)
    if n%10==0:print(json.dumps({'progress':n,'total':total,'variant':vn}),flush=True)
  sums=[]
  for vn in VARIANTS:
   for sp in ('ALL','DEV20','FRESH6'):
    rr=[r for r in rows if r['variant']==vn and (sp=='ALL' or r['evalSplit']==sp)];s=agg(rr);s.update({'variant':vn,'split':sp});sums.append(s)
  target={}
  for sp in ('ALL','DEV20','FRESH6'):
   rr=[c for c in co if sp=='ALL' or c.get('evalSplit')==sp];buy=sum(float(x['targetBuy']) for x in rr);p=sum(float(x['targetPnl']) for x in rr);target[sp]={'markets':len(rr),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'winRate':sum(float(x['targetPnl'])>0 for x in rr)/len(rr)}
  out={'version':'ETH_MAKER_PLACEMENT_TEACHER_HFT_EVAL26_V1','boundary':['Maker-only closed loop; no Target actions enter decisions','model trained with all 26 eval markets blocked from train/validation/test','750ms entry/250ms response risk queue fixed before run','<=180s no new entry','price/qty heads retained despite weak offline generalization; causal credit must not be assigned to them','TTL is execution sensitivity because cancel teacher is not yet learned'],'target':target,'summaries':sums,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'target':target,'summaries':sums},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
