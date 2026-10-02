from __future__ import annotations
import argparse,json,lzma,tempfile,zipfile,shutil,statistics,sys,math
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,average_precision_score
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as feed
EPS=1e-9;TTL=5000;SEEDS=('FORCE_UP','FORCE_DOWN')
FEATURES=['seconds_left','up_bid','up_ask','down_bid','down_ask','spread','bid_depth','ask_depth','top3_bid','top3_ask','book_imbalance','order_count','gross','net','abs_net','imbalance_ratio','pair_coverage','floor','best_pnl','avg_cost_up','avg_cost_down','weak_side_up','weak_gap','weak_bid','weak_ask','dom_bid','dom_ask','marginal_pair_sum_weak','pair_reserve','paired_qty','unmatched_up','unmatched_down','last_fill_age_ms','last_place_age_ms','fills_5s','fills_15s','shares_10s','update_add','update_cut','update_bid_add','update_ask_add','update_bid_cut','update_ask_cut']
def live(s):return s in {'NEW','PARTIALLY_FILLED'}
def fill_price(side,s,fallback):
 p=s.get('execPrice');
 if p is None:return float(fallback)
 p=float(p);return p if side=='UP' else 1-p
def apply(book,u):
 ca={'add':0.,'cut':0.,'bidadd':0.,'askadd':0.,'bidcut':0.,'askcut':0.}
 if int(u[3]):book['bids']={float(k):float(v) for k,v in (u[4] or {}).items()};book['asks']={float(k):float(v) for k,v in (u[5] or {}).items()};return ca
 for key in ('bids','asks'):
  for r in (u[6] or {}).get(key,[]) or []:
   p=float(r[0]);before=float(r[1]);after=float(r[2]);delta=after-before
   if delta>0:ca['add']+=delta;ca['bidadd' if key=='bids' else 'askadd']+=delta
   elif delta<0:ca['cut']+=-delta;ca['bidcut' if key=='bids' else 'askcut']+=-delta
   if after<=EPS:book[key].pop(p,None)
   else:book[key][p]=after
 return ca
def quotes(book):
 if not book['bids'] or not book['asks']:return None
 bb=max(book['bids']);ba=min(book['asks']);bd=float(book['bids'][bb]);ad=float(book['asks'][ba]);tb=sum(book['bids'][p] for p in sorted(book['bids'],reverse=True)[:3]);ta=sum(book['asks'][p] for p in sorted(book['asks'])[:3]);return {'UP':{'bid':float(bb),'ask':float(ba)},'DOWN':{'bid':1-float(ba),'ask':1-float(bb)},'bd':bd,'ad':ad,'tb':float(tb),'ta':float(ta),'imb':(tb-ta)/(tb+ta+1e-9),'spread':float(ba-bb)}
class Sim:
 def __init__(self,tape,traj=None,seed='FORCE_UP'):
  self.payload=json.loads(lzma.decompress(tape.read_bytes()).decode('utf-8'));feed.ARCHIVE_DIR=tape.parent;self.events,self.times,self.meta=feed.build_archive_events(int(self.payload['marketId']),trade_offset='mid');self.bt=ex.new_bt(self.events,entry_latency_ms=250,response_latency_ms=250,queue_model='risk');ex.initialize_bt(self.bt);self.traj=sorted(traj or [],key=lambda r:r['t']);self.ti=0;self.target={'UP':0.,'DOWN':0.};self.seed=seed;self.seeded=False;self.firstValid=None;self.book={'bids':{},'asks':{}};self.orders={};self.n=1;self.inv={'UP':0.,'DOWN':0.};self.cost=0.;self.sideCost={'UP':0.,'DOWN':0.};self.un={'UP':deque(),'DOWN':deque()};self.pairReserve=0.;self.pairedQty=0.;self.fillHist=deque();self.placeHist=deque();self.submits=0;self.fills=0
 def close(self):self.bt.close()
 def snap(self,o):return ex.order_snapshot(self.bt,o['n'])
 def record_fill(self,t,side,q,p):
  self.inv[side]+=q;self.cost+=q*p;self.sideCost[side]+=q*p;opp='DOWN' if side=='UP' else 'UP';left=q
  while left>EPS and self.un[opp]:
   oq,op=self.un[opp][0];m=min(left,oq);self.pairReserve+=m*(1-p-op);self.pairedQty+=m;left-=m;oq-=m
   if oq<=EPS:self.un[opp].popleft()
   else:self.un[opp][0]=(oq,op)
  if left>EPS:self.un[side].append((left,p))
  self.fillHist.append((t,side,q,p))
 def process(self,t):
  for o in self.orders.values():
   s=self.snap(o);cum=float(s.get('cumExecQty') or 0);inc=max(0.,cum-o['cum'])
   if inc>EPS:self.record_fill(t,o['side'],inc,fill_price(o['side'],s,o['price']));self.fills+=1;o['cum']=cum
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
   if o['side']==side:
    s=self.snap(o)
    if live(s.get('status')):z+=max(0.,float(s.get('leavesQty') or 0))
  return z
 def submit(self,t,side,p,q):
  n=self.n;self.n+=1;ex.submit_native(self.bt,n,side,float(p),float(q));self.orders[f'{side}_{n}']={'n':n,'side':side,'price':float(p),'qty':float(q),'cum':0.,'placed':t,'status':'NEW'};self.placeHist.append((t,side,q,p));self.submits+=1
 def advance_target(self,t):
  while self.ti<len(self.traj) and int(self.traj[self.ti]['t'])<t:
   r=self.traj[self.ti];self.target[r['side']]+=float(r['shares']);self.ti+=1
 def unmatched_avg(self,side):
  q=sum(a for a,_ in self.un[side]);return sum(a*p for a,p in self.un[side])/q if q>EPS else None
 def econ_ok(self,side,p,q):
  opp='DOWN' if side=='UP' else 'UP';oppq=sum(a for a,_ in self.un[opp]);sameq=sum(a for a,_ in self.un[side]);target_abs=abs(self.target['UP']-self.target['DOWN']);max_un=max(10.,target_abs)
  if oppq<=EPS:return sameq+q<=max_un+1e-9
  avg=self.unmatched_avg(opp);return avg is not None and avg+p<=1.0000001
 def oracle_action(self,qv):
  ds=[]
  for side in ('UP','DOWN'):
   d=max(0.,self.target[side]-self.inv[side]-self.reserved(side));ds.append((d,side))
  for d,side in sorted(ds,reverse=True):
   if d<=.25:continue
   p=float(qv[side]['bid']);legal=1/p if p>0 else 1e9;qty=d
   if qty<legal:
    if qty<.5*legal:continue
    qty=legal
   qty=min(qty,12.)
   if self.econ_ok(side,p,qty):return 1,side,qty
  return 0,'UP',0.
 def seed_if_needed(self,t,qv):
  if self.seeded or self.firstValid is None or t-self.firstValid<2000:return
  side='UP' if self.seed=='FORCE_UP' else 'DOWN';p=float(qv[side]['bid']);qty=max(5.,1/p);self.submit(t,side,p,qty);self.seeded=True
 def features(self,t,qv,ca,end):
  while self.fillHist and t-self.fillHist[0][0]>30000:self.fillHist.popleft()
  while self.placeHist and t-self.placeHist[0][0]>30000:self.placeHist.popleft()
  u,d=self.inv['UP'],self.inv['DOWN'];gross=u+d;net=u-d;ab=abs(net);base=min(u,d);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
  avgup=self.sideCost['UP']/u if u>EPS else 0.;avgdn=self.sideCost['DOWN']/d if d>EPS else 0.;wb=qv[weak]['bid'] if weak else 0.;wa=qv[weak]['ask'] if weak else 0.;db=qv[dom]['bid'] if dom else 0.;da=qv[dom]['ask'] if dom else 0.;avgdom=avgdn if dom=='DOWN' else avgup if dom=='UP' else 0.;lastf=self.fillHist[-1] if self.fillHist else None;lastp=self.placeHist[-1] if self.placeHist else None;r5=[x for x in self.fillHist if t-x[0]<=5000];r15=[x for x in self.fillHist if t-x[0]<=15000];r10=[x for x in self.fillHist if t-x[0]<=10000]
  f={'seconds_left':(end-t)/1000.,'up_bid':qv['UP']['bid'],'up_ask':qv['UP']['ask'],'down_bid':qv['DOWN']['bid'],'down_ask':qv['DOWN']['ask'],'spread':qv['spread'],'bid_depth':qv['bd'],'ask_depth':qv['ad'],'top3_bid':qv['tb'],'top3_ask':qv['ta'],'book_imbalance':qv['imb'],'order_count':0.,'gross':gross,'net':net,'abs_net':ab,'imbalance_ratio':ab/gross if gross else 0.,'pair_coverage':2*base/gross if gross else 0.,'floor':base-self.cost,'best_pnl':max(u,d)-self.cost,'avg_cost_up':avgup,'avg_cost_down':avgdn,'weak_side_up':1. if weak=='UP' else -1. if weak=='DOWN' else 0.,'weak_gap':ab,'weak_bid':wb,'weak_ask':wa,'dom_bid':db,'dom_ask':da,'marginal_pair_sum_weak':avgdom+wa if weak else 0.,'pair_reserve':self.pairReserve,'paired_qty':self.pairedQty,'unmatched_up':sum(a for a,_ in self.un['UP']),'unmatched_down':sum(a for a,_ in self.un['DOWN']),'last_fill_age_ms':t-lastf[0] if lastf else 1e6,'last_place_age_ms':t-lastp[0] if lastp else 1e6,'fills_5s':float(len(r5)),'fills_15s':float(len(r15)),'shares_10s':float(sum(x[2] for x in r10)),'update_add':ca['add'],'update_cut':ca['cut'],'update_bid_add':ca['bidadd'],'update_ask_add':ca['askadd'],'update_bid_cut':ca['bidcut'],'update_ask_cut':ca['askcut']};return np.asarray([float(f[k]) for k in FEATURES],np.float32)
 def run_oracle_collect(self):
  X=[];ya=[];ys=[];yq=[];ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  for u in ups:
   t=int(u[1]);ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=apply(self.book,u);self.advance_target(t);qv=quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   x=self.features(t,qv,ca,end);act,side,qty=self.oracle_action(qv);X.append(x);ya.append(act);ys.append(1 if side=='UP' else 0);yq.append(math.log1p(qty) if act else 0.)
   if act:
    p=float(qv[side]['bid']);self.submit(t,side,p,qty)
  return X,ya,ys,yq
 def run_student(self,models,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  for u in ups:
   t=int(u[1]);ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=apply(self.book,u);qv=quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
   if pa<models['actionTh']:continue
   ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.);self.submit(t,side,p,qty)
  end2=int(self.meta['lastReceivedMs']);ex.advance_to(self.bt,end2);self.process(end2);pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());return {'pnl':pnl,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'floor':min(self.inv.values())-self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'up':self.inv['UP'],'down':self.inv['DOWN']}
def fit_models(X,yA,yS,yQ,market):
 X=np.asarray(X,np.float32);yA=np.asarray(yA,int);yS=np.asarray(yS,int);yQ=np.asarray(yQ,float);market=np.asarray(market,int);ums=sorted(set(market));tr=set(ums[:30]);va=set(ums[30:40]);it=np.where(np.isin(market,list(tr)))[0];iv=np.where(np.isin(market,list(va)))[0];action=HistGradientBoostingClassifier(max_iter=260,learning_rate=.04,max_leaf_nodes=23,min_samples_leaf=40,l2_regularization=4.,class_weight='balanced',random_state=1).fit(X[it],yA[it]);pv=action.predict_proba(X[iv])[:,1];rate=float(yA[iv].mean());ath=float(np.quantile(pv,1-max(.001,min(.999,rate))));posa=it[yA[it]==1];posv=iv[yA[iv]==1];side=HistGradientBoostingClassifier(max_iter=220,learning_rate=.04,max_leaf_nodes=19,min_samples_leaf=20,l2_regularization=3.,class_weight='balanced',random_state=2).fit(X[posa],yS[posa]);psv=side.predict_proba(X[posv])[:,1];sr=float(yS[posv].mean());sth=float(np.quantile(psv,1-max(.001,min(.999,sr))));qty=HistGradientBoostingRegressor(max_iter=220,learning_rate=.04,max_leaf_nodes=19,min_samples_leaf=20,l2_regularization=3.,random_state=3).fit(X[posa],yQ[posa]);metrics={'validation':{'actionN':int(len(iv)),'actionRate':rate,'actionAuc':float(roc_auc_score(yA[iv],pv)) if len(set(yA[iv]))>1 else None,'actionAP':float(average_precision_score(yA[iv],pv)) if yA[iv].sum()>0 else None,'sideN':int(len(posv)),'sideAuc':float(roc_auc_score(yS[posv],psv)) if len(set(yS[posv]))>1 else None}};return {'action':action,'side':side,'qty':qty,'actionTh':ath,'sideTh':sth},metrics
def agg(rs):
 buy=sum(r['buyNotional'] for r in rs);p=sum(r['pnl'] for r in rs);a=[r for r in rs if r['buyNotional']>EPS];return {'markets':len(rs),'activeMarkets':len(a),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'activeWinRate':sum(r['pnl']>0 for r in a)/len(a) if a else None,'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_dagger60_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));train=[r for r in cohort if r['split']=='TRAIN40'];test=[r for r in cohort if r['split']=='TEST20'];X=[];yA=[];yS=[];yQ=[];mids=[]
  for i,cr in enumerate(train,1):
   for seed in SEEDS:
    sim=Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
    try:x,a1,s,q=sim.run_oracle_collect()
    finally:sim.close()
    X.extend(x);yA.extend(a1);yS.extend(s);yQ.extend(q);mids.extend([int(cr['marketId'])]*len(x))
   if i%10==0:print(json.dumps({'collectProgress':i,'of':len(train),'rows':len(X),'actions':sum(yA)}),flush=True)
  models,offline=fit_models(X,yA,yS,yQ,mids);rows=[]
  for seed in SEEDS:
   for i,cr in enumerate(test,1):
    sim=Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",None,seed)
    try:r=sim.run_student(models,cr['winner'])
    finally:sim.close()
    r.update({'marketId':int(cr['marketId']),'seed':seed,'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy']});rows.append(r)
  sums=[]
  for seed in SEEDS:
   s=agg([r for r in rows if r['seed']==seed]);s['seed']=seed;sums.append(s)
  targetBuy=sum(r['targetBuy'] for r in test);targetP=sum(r['targetPnl'] for r in test);target={'markets':len(test),'pnl':targetP,'buyNotional':targetBuy,'roi':targetP/targetBuy if targetBuy else None,'winRate':sum(r['targetPnl']>0 for r in test)/len(test)}
  out={'version':'ETH_DAGGER60_SMOKE_V1','boundary':['development-only on-policy oracle distillation','TRAIN40: our HFT states generated under FORCE_UP and FORCE_DOWN perturbation seeds; Target Maker strict-past objective + hard pair<=1 supplies oracle action labels','TEST20: Target objective/trajectory completely absent from student decisions; only direct seed + public book + own inventory/history','<=180s no new exposure; Maker-only best-bid post-only, 5s TTL'],'features':FEATURES,'trainingRows':len(X),'trainingActions':int(sum(yA)),'offline':offline,'targetTest20':target,'summaries':sums,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'trainingRows':len(X),'trainingActions':int(sum(yA)),'offline':offline,'target':target,'summaries':sums},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
