from __future__ import annotations
import argparse,bisect,collections,json,math,sqlite3,time,zlib
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss

EPS=1e-9;TICK=0.01;SIDES=('UP','DOWN')
def opp(s): return 'DOWN' if s=='UP' else 'UP'
def dec(blob): return json.loads(zlib.decompress(blob).decode('utf-8')) if blob else None

def qstats(xs):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return {'n':0}
 def q(p):
  z=(len(ys)-1)*p;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
  return ys[lo]*(1-w)+ys[hi]*w
 return {'n':len(ys),'mean':float(sum(ys)/len(ys)),'median':float(q(.5)),'p10':float(q(.1)),'p90':float(q(.9)),'max':float(max(ys))}

def apply_changes(book,chg):
 if not isinstance(chg,dict):return
 for k in ('bids','asks'):
  for x in chg.get(k,[]) or []:
   p=float(x['price']);a=float(x['after'])
   if a<=EPS:book[k].pop(p,None)
   else:book[k][p]=a

def topn(d,n,reverse): return float(sum(v for _,v in sorted(d.items(),key=lambda kv:kv[0],reverse=reverse)[:n]))

def snap_from_book(t,book):
 bids,asks=book['bids'],book['asks']
 if not bids or not asks:return None
 ub=max(bids);ua=min(asks);ubd=float(bids[ub]);uad=float(asks[ua])
 # Native book is UP; DOWN is complement.
 return {'t':int(t),
  'UP':{'bid':ub,'ask':ua,'bidDepth':ubd,'askDepth':uad,'top3Bid':topn(bids,3,True),'top3Ask':topn(asks,3,False)},
  'DOWN':{'bid':1.0-ua,'ask':1.0-ub,'bidDepth':uad,'askDepth':ubd,'top3Bid':topn(asks,3,False),'top3Ask':topn(bids,3,True)}}

def load_book_snaps(c,mid):
 book={'bids':{},'asks':{}};sn=[]
 for u in c.execute('select received_at_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by received_at_ms,id',(mid,)):
  if int(u['is_checkpoint']):
   book={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
  else:apply_changes(book,dec(u['changes_z']) or {})
  z=snap_from_book(int(u['received_at_ms']),book)
  if z:sn.append(z)
 return sn

def snap_before(sn,t):
 if not sn:return None
 ts=[x['t'] for x in sn];i=bisect.bisect_left(ts,int(t))-1
 return sn[i] if i>=0 else None

def snap_at_or_before(sn,t):
 if not sn:return None
 ts=[x['t'] for x in sn];i=bisect.bisect_right(ts,int(t))-1
 return sn[i] if i>=0 else None

def book_feats(sn,t,expand_side,oldest_px,wavg_px):
 cur=snap_before(sn,t)
 if cur is None:return None
 age=int(t)-int(cur['t'])
 if age<0 or age>2000:return None
 rep=opp(expand_side);R=cur[rep];E=cur[expand_side]
 past1=snap_at_or_before(sn,int(t)-1000);past3=snap_at_or_before(sn,int(t)-3000)
 def mv(past,side,field,curv):
  if past is None:return math.nan
  try:return (float(curv)-float(past[side][field]))/TICK if field in ('bid','ask') else float(curv)-float(past[side][field])
  except:return math.nan
 inside=float(R['ask']-TICK) if (R['ask']-R['bid'])>1.5*TICK else math.nan
 out={
  'bookAgeMs':float(age),
  'repairBid':float(R['bid']),'repairAsk':float(R['ask']),'repairSpreadTicks':float((R['ask']-R['bid'])/TICK),
  'repairBidDepth':float(R['bidDepth']),'repairAskDepth':float(R['askDepth']),'repairTop3Bid':float(R['top3Bid']),'repairTop3Ask':float(R['top3Ask']),
  'expandBid':float(E['bid']),'expandAsk':float(E['ask']),'expandSpreadTicks':float((E['ask']-E['bid'])/TICK),
  'expandBidDepth':float(E['bidDepth']),'expandAskDepth':float(E['askDepth']),'expandTop3Bid':float(E['top3Bid']),'expandTop3Ask':float(E['top3Ask']),
  'repairMinusExpandAsk':float(R['ask']-E['ask']),'repairMinusExpandBid':float(R['bid']-E['bid']),
  'pairAskSum':float(R['ask']+E['ask']),'pairBidSum':float(R['bid']+E['bid']),
  'oldestPairAtRepairBid':float(oldest_px+R['bid']),'oldestPairAtRepairAsk':float(oldest_px+R['ask']),
  'weightedPairAtRepairBid':float(wavg_px+R['bid']),'weightedPairAtRepairAsk':float(wavg_px+R['ask']),
  'insidePassiveAvailable':1.0 if math.isfinite(inside) else 0.0,
  'oldestPairAtInside':float(oldest_px+inside) if math.isfinite(inside) else math.nan,
  'weightedPairAtInside':float(wavg_px+inside) if math.isfinite(inside) else math.nan,
  'repairBidMove1s':mv(past1,rep,'bid',R['bid']),'repairAskMove1s':mv(past1,rep,'ask',R['ask']),
  'repairBidDepthChange1s':mv(past1,rep,'bidDepth',R['bidDepth']),'repairAskDepthChange1s':mv(past1,rep,'askDepth',R['askDepth']),
  'repairBidMove3s':mv(past3,rep,'bid',R['bid']),'repairAskMove3s':mv(past3,rep,'ask',R['ask']),
  'repairBidDepthChange3s':mv(past3,rep,'bidDepth',R['bidDepth']),'repairAskDepthChange3s':mv(past3,rep,'askDepth',R['askDepth'])}
 return out

TOPO=['logQty','logAge','lotCount','oldestShare','priorNoRepairClocks','logSinceRepair','sinceRepairMissing','prior_NONE','prior_REPAIR_PLUS_EXPAND','prior_REPAIR_PRESENT_NO_NEW_EXPAND','prior_EXPAND_ONLY_WITH_DEBT']
BOOK=['bookAgeMs','repairBid','repairAsk','repairSpreadTicks','repairBidDepth','repairAskDepth','repairTop3Bid','repairTop3Ask','expandBid','expandAsk','expandSpreadTicks','expandBidDepth','expandAskDepth','expandTop3Bid','expandTop3Ask','repairMinusExpandAsk','repairMinusExpandBid','pairAskSum','pairBidSum','repairBidMove1s','repairAskMove1s','repairBidDepthChange1s','repairAskDepthChange1s','repairBidMove3s','repairAskMove3s','repairBidDepthChange3s','repairAskDepthChange3s']
EXEC=['oldestPrice','weightedDebtPrice','oldestPairAtRepairBid','oldestPairAtRepairAsk','weightedPairAtRepairBid','weightedPairAtRepairAsk','insidePassiveAvailable','oldestPairAtInside','weightedPairAtInside','repairSideFillClocks1s','expandSideFillClocks1s','repairSideFillQty1s','expandSideFillQty1s','repairSideFillClocks3s','expandSideFillClocks3s','repairSideFillQty3s','expandSideFillQty3s']
SETS={'TOPOLOGY_ONLY':TOPO,'BOOK_LIFECYCLE_ONLY':BOOK,'TOPOLOGY_PLUS_BOOK':TOPO+BOOK,'TOPOLOGY_PLUS_EXECUTION_GEOMETRY':TOPO+BOOK+EXEC}

def recent_progress(history,t,rep,exp,w):
 z=[x for x in history if int(t)-w<int(x['t'])<int(t)]
 return {'repairClocks':sum(1 for x in z if x['side']==rep),'expandClocks':sum(1 for x in z if x['side']==exp),'repairQty':sum(x['q'] for x in z if x['side']==rep),'expandQty':sum(x['q'] for x in z if x['side']==exp)}

def build_rows(target_db,book_db,max_markets=0,market_offset=0):
 tc=sqlite3.connect(f'file:{Path(target_db).resolve().as_posix()}?mode=ro',uri=True);tc.row_factory=sqlite3.Row
 bc=sqlite3.connect(f'file:{Path(book_db).resolve().as_posix()}?mode=ro',uri=True);bc.row_factory=sqlite3.Row
 bm={int(r[0]) for r in bc.execute('select distinct market_id from maker_book_inference_updates')}
 qual={int(r[0]) for r in bc.execute('select market_id from maker_execution_market_quality_v1 where eligible_execution_training=1')}
 mids=[int(r[0]) for r in tc.execute("select distinct market_id from wallet_shadow_target_events where asset='ETH' order by market_id") if int(r[0]) in bm]
 mids=mids[int(market_offset):]
 if max_markets>0:mids=mids[:max_markets]
 rows=[];viol=collections.Counter();book_ages=[];market_first={}
 try:
  for mi,mid in enumerate(mids,1):
   ev=list(tc.execute("select id,event_ms,side,price,shares from wallet_shadow_target_events where asset='ETH' and market_id=? order by event_ms,id",(mid,)))
   if not ev:continue
   clocks=collections.defaultdict(list)
   for r in ev:clocks[int(r['event_ms'])].append(r)
   sn=load_book_snaps(bc,mid)
   if not sn:continue
   market_first[mid]=min(clocks);qs={s:collections.deque() for s in SIDES};out={s:0.0 for s in SIDES};hold={s:0.0 for s in SIDES};lot_id=0;prior_no_rep=0;last_rep=None;last_tr='NONE';hist=[]
   for t,legs in sorted(clocks.items()):
    pre_total=out['UP']+out['DOWN'];pre_lots=len(qs['UP'])+len(qs['DOWN']);exp='UP' if out['UP']>EPS else ('DOWN' if out['DOWN']>EPS else None)
    oldest=qs[exp][0] if exp else None;oldrem=float(oldest['remaining']) if oldest else 0.0;oldpx=float(oldest['price']) if oldest else math.nan;age=t-int(oldest['bornAt']) if oldest else 0;oshare=oldrem/pre_total if pre_total>EPS else 0.0
    if exp and pre_total>EPS:
     wavg=sum(float(x['remaining'])*float(x['price']) for x in qs[exp])/pre_total
    else:wavg=math.nan
    since=t-last_rep if last_rep is not None else None
    # Current Target legs remain labels/outcomes only; construct per-leg remaining for exact FIFO payment.
    incoming={s:[] for s in SIDES}
    for r in legs:
     s=str(r['side']).upper();q=float(r['shares']);p=float(r['price'])
     if s in SIDES and q>EPS:incoming[s].append({'q':q,'price':p,'id':int(r['id'])})
    repair={s:0.0 for s in SIDES}
    for pay in SIDES:
     dq=qs[opp(pay)]
     for leg in incoming[pay]:
      need=leg['q']
      while need>EPS and dq:
       lot=dq[0];take=min(need,float(lot['remaining']))
       lot['remaining']-=take;out[opp(pay)]-=take;repair[pay]+=take;need-=take
       if lot['remaining']<=EPS:dq.popleft()
      leg['q']=need
    # same-clock direct pair of residual acquisitions, preserving source-leg residual prices
    ui=di=0
    while ui<len(incoming['UP']) and di<len(incoming['DOWN']):
     u=incoming['UP'][ui];d=incoming['DOWN'][di];take=min(u['q'],d['q']);u['q']-=take;d['q']-=take
     if u['q']<=EPS:ui+=1
     if d['q']<=EPS:di+=1
    birth=0.0
    for s in SIDES:
     for leg in incoming[s]:
      q=float(leg['q'])
      if q<=EPS:continue
      lot_id+=1;qs[s].append({'id':lot_id,'side':s,'bornAt':t,'remaining':q,'price':float(leg['price'])});out[s]+=q;birth+=q
    rep=repair['UP']+repair['DOWN'];post=out['UP']+out['DOWN']
    if pre_total>EPS and (rep>EPS or birth>EPS):
     tr='REPAIR_PLUS_EXPAND' if (rep>EPS and birth>EPS) else ('REPAIR_PRESENT_NO_NEW_EXPAND' if rep>EPS else 'EXPAND_ONLY_WITH_DEBT')
     bf=book_feats(sn,t,exp,oldpx,wavg)
     if bf is not None:
      book_ages.append(bf['bookAgeMs']);p1=recent_progress(hist,t,opp(exp),exp,1000);p3=recent_progress(hist,t,opp(exp),exp,3000)
      row={'marketId':mid,'t':t,'qualityMarket':1 if mid in qual else 0,'logQty':math.log1p(pre_total),'logAge':math.log1p(max(0,age)/1000.0),'lotCount':float(pre_lots),'oldestShare':float(oshare),'priorNoRepairClocks':float(prior_no_rep),'logSinceRepair':math.log1p(max(0,since)/1000.0) if since is not None else 0.0,'sinceRepairMissing':1.0 if since is None else 0.0,'oldestPrice':oldpx,'weightedDebtPrice':wavg,
       'prior_NONE':1.0 if last_tr=='NONE' else 0.0,'prior_REPAIR_PLUS_EXPAND':1.0 if last_tr=='REPAIR_PLUS_EXPAND' else 0.0,'prior_REPAIR_PRESENT_NO_NEW_EXPAND':1.0 if last_tr=='REPAIR_PRESENT_NO_NEW_EXPAND' else 0.0,'prior_EXPAND_ONLY_WITH_DEBT':1.0 if last_tr=='EXPAND_ONLY_WITH_DEBT' else 0.0,
       'repairSideFillClocks1s':float(p1['repairClocks']),'expandSideFillClocks1s':float(p1['expandClocks']),'repairSideFillQty1s':float(p1['repairQty']),'expandSideFillQty1s':float(p1['expandQty']),'repairSideFillClocks3s':float(p3['repairClocks']),'expandSideFillClocks3s':float(p3['expandClocks']),'repairSideFillQty3s':float(p3['repairQty']),'expandSideFillQty3s':float(p3['expandQty']),
       **bf,'service':1 if rep>EPS else 0}
      rows.append(row)
     if rep>EPS:prior_no_rep=0;last_rep=t
     else:prior_no_rep+=1
     last_tr=tr
    elif pre_total<=EPS:
     prior_no_rep=0;last_tr='NONE'
    # history is strict-past for future clocks; current acquisitions become past only after label row creation
    for s in SIDES:
     q=sum(float(r['shares']) for r in legs if str(r['side']).upper()==s)
     if q>EPS:hist.append({'t':t,'side':s,'q':q})
    hold['UP']+=sum(float(r['shares']) for r in legs if str(r['side']).upper()=='UP');hold['DOWN']+=sum(float(r['shares']) for r in legs if str(r['side']).upper()=='DOWN')
    gap=abs(hold['UP']-hold['DOWN'])
    if abs(post-gap)>1e-6:viol['outstandingGapMismatch']+=1
    if out['UP']>EPS and out['DOWN']>EPS:viol['twoOutstandingSides']+=1
   if mi%100==0:print(json.dumps({'progressMarkets':mi,'of':len(mids),'rows':len(rows)},ensure_ascii=False),flush=True)
 finally:
  tc.close();bc.close()
 return rows,market_first,dict(viol),qstats(book_ages)

def split(mf):
 mids=[m for m,_ in sorted(mf.items(),key=lambda kv:(kv[1],kv[0]))];n=len(mids);a=max(1,int(.70*n));b=max(a+1,int(.85*n));return set(mids[:a]),set(mids[a:b]),set(mids[b:])
def mat(rows,features):return np.asarray([[float(r.get(k,math.nan)) if r.get(k) is not None else math.nan for k in features] for r in rows],np.float32)
def met(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'rocAuc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if int(y.sum()) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None,'brier':float(brier_score_loss(y,p)) if len(y) else None}
def fit_one(train,val,test,features):
 m=HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=40,l2_regularization=2.0,class_weight='balanced',random_state=20260906)
 Xtr=mat(train,features);ytr=np.asarray([r['service'] for r in train],int);m.fit(Xtr,ytr)
 out={}
 for name,rr in [('train',train),('validation',val),('test',test)]:
  y=np.asarray([r['service'] for r in rr],int);p=m.predict_proba(mat(rr,features))[:,1];out[name]=met(y,p)
 qtest=[r for r in test if r['qualityMarket']==1]
 if qtest:
  y=np.asarray([r['service'] for r in qtest],int);p=m.predict_proba(mat(qtest,features))[:,1];out['qualityTest']=met(y,p)
 else:out['qualityTest']={'n':0}
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--target-db',default='data/target_wallet_official_v1.db');ap.add_argument('--book-db',default='data/wallet_maker_book_inference_eth5m.db');ap.add_argument('--output',required=True);ap.add_argument('--max-markets',type=int,default=0);ap.add_argument('--market-offset',type=int,default=0);a=ap.parse_args();ts=time.time()
 rows,mf,viol,bage=build_rows(a.target_db,a.book_db,a.max_markets,a.market_offset);trm,vam,tem=split(mf);train=[r for r in rows if r['marketId'] in trm];val=[r for r in rows if r['marketId'] in vam];test=[r for r in rows if r['marketId'] in tem]
 reps={name:fit_one(train,val,test,fs) for name,fs in SETS.items()}
 base=reps['TOPOLOGY_ONLY']['test'];full=reps['TOPOLOGY_PLUS_EXECUTION_GEOMETRY']['test'];gate=(full['rocAuc'] is not None and base['rocAuc'] is not None and full['rocAuc']-base['rocAuc']>=.03 and full['logLoss']<=base['logLoss']+1e-12)
 out={'version':'TARGET_ETH_FIFO_MICROSTRUCTURE_SERVICE_TEACHER_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'maxMarkets':a.max_markets,'runtimeSeconds':time.time()-ts,'coverage':{'rows':len(rows),'markets':len(mf),'bookAgeMs':bage,'qualityRows':sum(r['qualityMarket'] for r in rows)},'split':{'markets':{'train':len(trm),'validation':len(vam),'test':len(tem)},'rows':{'train':len(train),'validation':len(val),'test':len(test)}},'featureSets':SETS,'models':reps,'primaryGate':{'requiredAucLift':.03,'aucLift':None if base['rocAuc'] is None or full['rocAuc'] is None else full['rocAuc']-base['rocAuc'],'requiredNoLogLossWorsening':True,'passed':bool(gate)},'invariantViolations':viol,'boundary':['strict-past FIFO topology and public book only','book state latest received_at_ms strictly before labeled Target event clock; max age 2s','current Target event legs are labels/outcomes only','chronological market-level split','fixed HGB model; no hyperparameter/threshold sweep','winner/PnL/future action unused','no runtime authority/no NEW24-B']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'coverage':out['coverage'],'split':out['split'],'test':{k:v['test'] for k,v in reps.items()},'qualityTest':{k:v.get('qualityTest') for k,v in reps.items()},'gate':out['primaryGate'],'invariantViolations':viol},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
