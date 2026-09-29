from __future__ import annotations
import argparse,bisect,collections,json,math,sys,tempfile,zipfile
from pathlib import Path
import joblib,numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS;TICK=v3b.TICK;Q=v3b.qshadow;base=v3b.base

def topn(d,n,reverse):return float(sum(v for _,v in sorted(d.items(),key=lambda kv:kv[0],reverse=reverse)[:n]))
def snap_book(t,book):
 bids,asks=book.get('bids') or {},book.get('asks') or {}
 if not bids or not asks:return None
 ub=max(bids);ua=min(asks);ubd=float(bids[ub]);uad=float(asks[ua])
 return {'t':int(t),'UP':{'bid':float(ub),'ask':float(ua),'bidDepth':ubd,'askDepth':uad,'top3Bid':topn(bids,3,True),'top3Ask':topn(asks,3,False)},'DOWN':{'bid':float(1.0-ua),'ask':float(1.0-ub),'bidDepth':uad,'askDepth':ubd,'top3Bid':topn(asks,3,False),'top3Ask':topn(bids,3,True)}}
def hist_before(hist,t):
 z=[x for x in hist if int(x['t'])<int(t)];return z[-1] if z else None
def hist_at_or_before(hist,t):
 z=[x for x in hist if int(x['t'])<=int(t)];return z[-1] if z else None

def pstats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 if not ys:return {'n':0}
 ys.sort();return {'n':len(ys),'mean':sum(ys)/len(ys),'median':ys[len(ys)//2],'min':ys[0],'max':ys[-1]}

class Shadow(v3b.FifoAggregateResponsibilityLadderV3B):
 def __init__(self,tape,artifact):
  super().__init__(tape);self.teacher_art=artifact;self.teacher_model=artifact['model'];self.teacher_features=list(artifact['features']);self.teacher_rows=[];self.teacher_book_hist=[];self.teacher_transitions=[];self.teacher_preledger={}
 def _ledger_state(self):
  total=sum(float(x['remainingQty']) for s in ('UP','DOWN') for x in self.resp_queues[s]);side='UP' if sum(float(x['remainingQty']) for x in self.resp_queues['UP'])>EPS else ('DOWN' if sum(float(x['remainingQty']) for x in self.resp_queues['DOWN'])>EPS else None)
  lots=[x for x in self.resp_queues[side]] if side else []
  oldest=lots[0] if lots else None;wavg=sum(float(x['remainingQty'])*float(x['price']) for x in lots)/total if total>EPS else math.nan
  return {'total':total,'side':side,'lotCount':sum(len(self.resp_queues[s]) for s in ('UP','DOWN')),'oldestRemaining':float(oldest['remainingQty']) if oldest else 0.0,'oldestBornAt':int(oldest['bornAt']) if oldest else None,'oldestPrice':float(oldest['price']) if oldest else math.nan,'weightedPrice':wavg}
 def _ledger_batch(self,t,legs):
  pre=self._ledger_state();self.teacher_preledger[int(t)]=dict(pre);bp=len(self.resp_payment_rows);br=len(self.resp_all);super()._ledger_batch(t,legs);rep=sum(float(x['qty']) for x in self.resp_payment_rows[bp:]);birth=sum(float(x['initialQty']) for x in self.resp_all[br:])
  if pre['total']>EPS and (rep>EPS or birth>EPS):
   tr='REPAIR_PLUS_EXPAND' if rep>EPS and birth>EPS else ('REPAIR_PRESENT_NO_NEW_EXPAND' if rep>EPS else 'EXPAND_ONLY_WITH_DEBT');self.teacher_transitions.append({'t':int(t),'tr':tr,'repair':rep>EPS})
 def _prior_transition_features(self,t):
  z=[x for x in self.teacher_transitions if int(x['t'])<int(t)];last=z[-1]['tr'] if z else 'NONE';nr=0
  for x in reversed(z):
   if x['repair']:break
   nr+=1
  last_rep=next((int(x['t']) for x in reversed(z) if x['repair']),None)
  return last,nr,last_rep
 def _recent_fill(self,t,side,w):
  xs=[x for x in self.fill_accounting if int(x['t'])<int(t) and int(x['t'])>int(t)-int(w) and str(x['side'])==str(side)]
  return len({int(x['t']) for x in xs}),sum(float(x['confirmedQty']) for x in xs)
 def _book_features(self,t,expand_side,snap,book_age):
  rep=Q.opp(expand_side);R=snap[rep];E=snap[expand_side];past1=hist_at_or_before(self.teacher_book_hist,int(t)-1000);past3=hist_at_or_before(self.teacher_book_hist,int(t)-3000)
  def mv(past,side,field,cur):
   if past is None:return math.nan
   try:return (float(cur)-float(past[side][field]))/TICK if field in ('bid','ask') else float(cur)-float(past[side][field])
   except:return math.nan
  state=self._ledger_state();oldpx=state['oldestPrice'];wpx=state['weightedPrice'];inside=float(R['ask']-TICK) if float(R['ask']-R['bid'])>1.5*TICK else math.nan
  return {'bookAgeMs':float(book_age),'repairBid':R['bid'],'repairAsk':R['ask'],'repairSpreadTicks':(R['ask']-R['bid'])/TICK,'repairBidDepth':R['bidDepth'],'repairAskDepth':R['askDepth'],'repairTop3Bid':R['top3Bid'],'repairTop3Ask':R['top3Ask'],'expandBid':E['bid'],'expandAsk':E['ask'],'expandSpreadTicks':(E['ask']-E['bid'])/TICK,'expandBidDepth':E['bidDepth'],'expandAskDepth':E['askDepth'],'expandTop3Bid':E['top3Bid'],'expandTop3Ask':E['top3Ask'],'repairMinusExpandAsk':R['ask']-E['ask'],'repairMinusExpandBid':R['bid']-E['bid'],'pairAskSum':R['ask']+E['ask'],'pairBidSum':R['bid']+E['bid'],'oldestPairAtRepairBid':oldpx+R['bid'],'oldestPairAtRepairAsk':oldpx+R['ask'],'weightedPairAtRepairBid':wpx+R['bid'],'weightedPairAtRepairAsk':wpx+R['ask'],'insidePassiveAvailable':1.0 if math.isfinite(inside) else 0.0,'oldestPairAtInside':oldpx+inside if math.isfinite(inside) else math.nan,'weightedPairAtInside':wpx+inside if math.isfinite(inside) else math.nan,'repairBidMove1s':mv(past1,rep,'bid',R['bid']),'repairAskMove1s':mv(past1,rep,'ask',R['ask']),'repairBidDepthChange1s':mv(past1,rep,'bidDepth',R['bidDepth']),'repairAskDepthChange1s':mv(past1,rep,'askDepth',R['askDepth']),'repairBidMove3s':mv(past3,rep,'bid',R['bid']),'repairAskMove3s':mv(past3,rep,'ask',R['ask']),'repairBidDepthChange3s':mv(past3,rep,'bidDepth',R['bidDepth']),'repairAskDepthChange3s':mv(past3,rep,'askDepth',R['askDepth'])}
 def _feature_row(self,t,a,snap,book_age):
  st=self._ledger_state();total=st['total'];old=st['oldestRemaining'];last,nr,lastrep=self._prior_transition_features(t);r1q,r1=self._recent_fill(t,a['side'],1000);e1q,e1=self._recent_fill(t,a['expandSide'],1000);r3q,r3=self._recent_fill(t,a['side'],3000);e3q,e3=self._recent_fill(t,a['expandSide'],3000)
  row={'logQty':math.log1p(total),'logAge':math.log1p(max(0,int(t)-int(st['oldestBornAt'] or t))/1000.0),'lotCount':float(st['lotCount']),'oldestShare':old/total if total>EPS else 0.0,'priorNoRepairClocks':float(nr),'logSinceRepair':math.log1p(max(0,int(t)-int(lastrep))/1000.0) if lastrep is not None else 0.0,'sinceRepairMissing':1.0 if lastrep is None else 0.0,'prior_NONE':1.0 if last=='NONE' else 0.0,'prior_REPAIR_PLUS_EXPAND':1.0 if last=='REPAIR_PLUS_EXPAND' else 0.0,'prior_REPAIR_PRESENT_NO_NEW_EXPAND':1.0 if last=='REPAIR_PRESENT_NO_NEW_EXPAND' else 0.0,'prior_EXPAND_ONLY_WITH_DEBT':1.0 if last=='EXPAND_ONLY_WITH_DEBT' else 0.0,'oldestPrice':st['oldestPrice'],'weightedDebtPrice':st['weightedPrice'],'repairSideFillClocks1s':float(r1q),'expandSideFillClocks1s':float(e1q),'repairSideFillQty1s':float(r1),'expandSideFillQty1s':float(e1),'repairSideFillClocks3s':float(r3q),'expandSideFillClocks3s':float(e3q),'repairSideFillQty3s':float(r3),'expandSideFillQty3s':float(e3),**self._book_features(t,a['expandSide'],snap,book_age)}
  return row
 def _score(self,row):
  x=np.asarray([[float(row.get(k,math.nan)) if row.get(k) is not None else math.nan for k in self.teacher_features]],np.float32);return float(self.teacher_model.predict_proba(x)[0,1])
 def _open_one_option(self,t,qv,end):
  cur=snap_book(t,self.book);prev=hist_before(self.teacher_book_hist,t)
  if cur is not None:self.teacher_book_hist.append(cur);self.teacher_book_hist=[x for x in self.teacher_book_hist if int(x['t'])>=int(t)-5000]
  self._teacher_cur_snap=cur;self._teacher_prev_snap=prev
  return super()._open_one_option(t,qv,end)
 def _arm_for_open_qty(self,t,qv):
  return super()._arm_for_open_qty(t,qv)
 def _submit_role(self,t,side,role,p,q,proj,source):
  a=self.q_arm;before_n=self.n;eligible=bool(a is not None and 'passivePrice' in a and side==a.get('side') and role==a.get('role') and abs(float(p)-float(a.get('passivePrice')))<=EPS and self.q_ladder is None)
  fc=pc=None;diag=None
  if eligible:
   cur=getattr(self,'_teacher_cur_snap',None);prev=getattr(self,'_teacher_prev_snap',None)
   if cur is not None:
    fc=self._feature_row(t,a,cur,0.0)
    if prev is not None and int(t)-int(prev['t'])<=2000:pc=self._feature_row(t,a,prev,int(t)-int(prev['t']))
    ask=float(a['qv'][side]['ask']);bid=float(a['qv'][side]['bid']);inside=ask-TICK if ask-bid>1.5*TICK else math.nan;minq=1.0/inside if math.isfinite(inside) and inside>EPS else math.nan;st=self._ledger_state();old=st['oldestRemaining'];agg=st['total']
    live=[]
    for sid0,key,o,role0 in self._live_role_rows():
     if str(o.get('side'))==str(side) and role0 in {'ECONOMIC_CORE','SATELLITE_REPAIR'}:live.append({'key':key,'role':role0,'price':float(o.get('price') or 0),'remaining':max(0.0,float(o.get('qty') or 0)-float(o.get('cum') or 0))})
    diag={'t':int(t),'responsibilityId':int(a['responsibilityId']),'targetExpandSide':a['expandSide'],'repairSide':side,'role':role,'scoreCurrentReceipt':self._score(fc),'scorePreviousReceipt':self._score(pc) if pc is not None else None,'previousBookAgeMs':None if prev is None else int(t)-int(prev['t']),'aggregateOutstandingQty':agg,'oldestRemainingQty':old,'lotCount':st['lotCount'],'insideMinimumCarrierQty':minq,'crossLotServiceable':bool(math.isfinite(minq) and old+EPS<minq<=agg+EPS),'boundaryCrossingNeeded':bool(math.isfinite(minq) and agg+EPS<minq),'freeSlotsBeforeSubmit':int(self.max_slots-len(self.slot_key)),'liveRepairCarriers':live,'featureCurrent':fc,'expectedKey':f'{side}_{before_n}'}
  ok=super()._submit_role(t,side,role,p,q,proj,source)
  if ok and diag is not None and self.q_ladder is not None and self.q_ladder.get('passiveKey')==diag['expectedKey']:
   diag['managedPassiveSubmittedAtClock']=True;self.teacher_rows.append(diag)
  return ok
 def finalize_teacher_rows(self):
  pay=self.resp_payment_rows;events=self.q_events
  for r in self.teacher_rows:
   t=int(r['t']);exp=r['targetExpandSide'];rid=int(r['responsibilityId']);ps=[x for x in pay if str(x['expandSide'])==str(exp) and t<int(x['t'])<=t+5000];ps10=[x for x in pay if str(x['expandSide'])==str(exp) and t<int(x['t'])<=t+10000]
   r['paymentWithin5s']=bool(ps);r['paidQtyWithin5s']=sum(float(x['qty']) for x in ps);r['paymentWithin10s']=bool(ps10);r['paidQtyWithin10s']=sum(float(x['qty']) for x in ps10)
   sub=[x for x in events if x.get('event')=='QTY_FIFO_MANAGED_PASSIVE_SUBMIT' and int(x.get('t',-1))==t and int(x.get('originResponsibilityId',x.get('responsibilityId',-1)))==rid]
   r['managedPassiveSubmittedAtClock']=bool(sub)
  return self.teacher_rows

def pfields(r):
 ks=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors');return {k:r.get(k) for k in ks}
def eq(a,b):
 if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
 if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
 return a==b

def summarize(rows):
 if not rows:return {'n':0}
 def grp(name,pred):
  z=[r for r in rows if pred(r)];return {'n':len(z),'scoreCurrent':pstats([r['scoreCurrentReceipt'] for r in z]),'scorePrevious':pstats([r['scorePreviousReceipt'] for r in z]),'payment5sRate':sum(r['paymentWithin5s'] for r in z)/len(z) if z else None,'managedSubmitRate':sum(r['managedPassiveSubmittedAtClock'] for r in z)/len(z) if z else None}
 return {'all':grp('all',lambda r:True),'crossLot':grp('cross',lambda r:r['crossLotServiceable']),'boundaryCrossing':grp('boundary',lambda r:r['boundaryCrossingNeeded']),'paid5s':grp('paid',lambda r:r['paymentWithin5s']),'unpaid5s':grp('unpaid',lambda r:not r['paymentWithin5s']),'managed':grp('managed',lambda r:r['managedPassiveSubmittedAtClock'])}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--model',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];art=joblib.load(a.model);rows=[]
 with tempfile.TemporaryDirectory(prefix='service_teacher_shadow_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
  for mid in mids:
   tape=root/'tapes'/f'{mid}.json.xz';A=v3b.FifoAggregateResponsibilityLadderV3B(tape)
   try:ra=A.run_qty('__UNSCORED__')
   finally:A.close()
   B=Shadow(tape,art)
   try:rb=B.run_qty('__UNSCORED__');tr=B.finalize_teacher_rows()
   finally:B.close()
   parity={k:eq(pfields(ra)[k],pfields(rb)[k]) for k in pfields(ra)}
   rows.append({'marketId':mid,'behaviorParity':parity,'behaviorParityPassed':all(parity.values()),'teacherRows':tr,'teacherSummary':summarize(tr),'ledgerInvariantViolations':rb['quantityLedgerSummary']['invariantViolations']})
   print(json.dumps({'marketId':mid,'parity':all(parity.values()),'teacherSummary':summarize(tr),'ledgerViol':rb['quantityLedgerSummary']['invariantViolations']},ensure_ascii=False),flush=True)
 out={'version':'OUR_V3B_TARGET_SERVICE_TEACHER_SHADOW_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'markets':mids,'rows':rows,'allBehaviorParity':all(r['behaviorParityPassed'] for r in rows),'allLedgerInvariantsPass':all(not r['ledgerInvariantViolations'] for r in rows),'aggregate':summarize([x for r in rows for x in r['teacherRows']]),'boundary':['V3B physical behavior unchanged','Target teacher score diagnostic only','equivalent strict-past OUR exact FIFO + public book features','current-receipt and previous-receipt book scores both recorded','future 5s payment used only posthoc evaluation','no threshold/no PnL/winner/no NEW24-B/no runtime authority']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allBehaviorParity':out['allBehaviorParity'],'allLedgerInvariantsPass':out['allLedgerInvariantsPass'],'aggregate':out['aggregate']},ensure_ascii=False))
if __name__=='__main__':main()
