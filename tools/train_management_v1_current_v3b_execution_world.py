from __future__ import annotations
import argparse,collections,json,math,os,tempfile,zipfile
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,mean_absolute_error
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS;base=v3b.base
H=(3,5)
ROLES=('PROBE_CORE','ECONOMIC_CORE','SATELLITE_REPAIR','SATELLITE_EXPAND')
ROUTES=('PASSIVE','ACTIVE')

def safe(x,d=0.0):
 try:
  z=float(x);return z if math.isfinite(z) else d
 except:return d

def opp(s):return 'DOWN' if s=='UP' else 'UP'

class TrainingTraceSim(v3b.FifoAggregateResponsibilityLadderV3B):
 def __init__(self,tape):
  super().__init__(tape);self.training_rows=[];self._end_ms=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])

 def _state_row(self,t,side,role,price,qty,route,key,source):
  qv=base.v2.base.quotes(self.book) or {}
  u=float(self.inv['UP']);d=float(self.inv['DOWN']);gross=u+d;dom='UP' if u>d+EPS else 'DOWN' if d>u+EPS else 'FLAT'
  debt_up=float(sum(float(x['remainingQty']) for x in self.resp_queues['UP']));debt_dn=float(sum(float(x['remainingQty']) for x in self.resp_queues['DOWN']))
  oldest=None
  rs=opp(side)
  if self.resp_queues[rs]:oldest=self.resp_queues[rs][0]
  old_frac=0.0
  if oldest is not None and float(oldest.get('initialQty') or 0)>EPS:old_frac=float(oldest.get('paidQty') or 0)/float(oldest['initialQty'])
  live=[]
  pending=0
  for sid,k in self.slot_key.items():
   o=self.orders.get(k)
   if not o:continue
   live.append((sid,k,o,self.key_role.get(k,'UNASSIGNED')))
   if o.get('cancelRequested'):pending+=1
  side_live=sum(1 for _,_,o,_ in live if str(o.get('side'))==side)
  repair_live=sum(1 for _,_,o,r in live if r in {'ECONOMIC_CORE','SATELLITE_REPAIR'})
  expand_live=sum(1 for _,_,o,r in live if r=='SATELLITE_EXPAND')
  bid=safe((qv.get(side) or {}).get('bid'));ask=safe((qv.get(side) or {}).get('ask'));mid=(bid+ask)/2 if ask>0 else bid
  dbid=safe((qv.get(dom) or {}).get('bid')) if dom in {'UP','DOWN'} else 0.;dask=safe((qv.get(dom) or {}).get('ask')) if dom in {'UP','DOWN'} else 0.;dmid=(dbid+dask)/2 if dask>0 else dbid
  outstanding_for_side=float(sum(float(x['remainingQty']) for x in self.resp_queues[opp(side)]))
  return {
   't':int(t),'key':str(key),'side':str(side),'role':str(role),'route':str(route),'source':str(source),'price':float(price),'qty':float(qty),
   'secondsLeft':max(0.0,(self._end_ms-int(t))/1000.0),'upQty':u,'downQty':d,'cost':float(self.cost),'floor':float(min(u,d)-self.cost),'best':float(max(u,d)-self.cost),
   'absNet':abs(u-d),'coverage':(2*min(u,d)/gross if gross>EPS else 0.0),'gross':gross,'dominantSide':dom,
   'debtUp':debt_up,'debtDown':debt_dn,'totalDebt':debt_up+debt_dn,'targetDebtForActionSide':outstanding_for_side,
   'oldestRepairProgress':old_frac,'oldestRepairAgeMs':(int(t)-int(oldest['bornAt']) if oldest else 0),'responsibilityCount':len(self.resp_all),
   'liveSlots':len(live),'sideLiveSlots':side_live,'repairLiveSlots':repair_live,'expandLiveSlots':expand_live,'pendingCancelCount':pending,
   'bookImbalance':safe(qv.get('imb')),'spread':safe(qv.get('spread')),'sideBid':bid,'sideAsk':ask,'sideMid':mid,'dominantMid':dmid,
   'priceToBid':float(price)-bid,'askToPrice':ask-float(price),'pairLegal':1.0 if self._pair_ok(side,float(price)) else 0.0,
   'qLadderLive':1.0 if self.q_ladder is not None else 0.0,'qPendingActive':1.0 if self.q_pending_active is not None else 0.0,
   'isRepairRole':1.0 if role in {'ECONOMIC_CORE','SATELLITE_REPAIR'} else 0.0,'isExpandRole':1.0 if role=='SATELLITE_EXPAND' else 0.0,
   'sideIsDominant':1.0 if dom==side else 0.0,'sideIsWeak':1.0 if dom in {'UP','DOWN'} and dom!=side else 0.0,
  }

 def _submit_role(self,t,side,role,p,q,proj,source):
  before_n=self.n;ok=super()._submit_role(t,side,role,p,q,proj,source)
  if ok:
   key=f'{side}_{before_n}';self.training_rows.append(self._state_row(t,side,role,p,q,'PASSIVE',key,source))
  return ok

 def _submit_protected_active_qty(self,t,qv):
  before_n=self.n;before=len(self.q_events);ok=super()._submit_protected_active_qty(t,qv)
  if ok:
   ev=next((x for x in reversed(self.q_events[before:]) if x.get('event')=='QTY_FIFO_MANAGED_ACTIVE_SUBMIT'),None)
   if ev:
    key=str(ev['key']);self.training_rows.append(self._state_row(t,str(ev['side']),str(ev['role']),float(ev['limitPrice']),float(ev['qty']),'ACTIVE',key,'PROTECTED_ACTIVE'))
  return ok

 def finalize_labels(self):
  fills=collections.defaultdict(list)
  for x in self.fill_accounting:fills[str(x['key'])].append(x)
  cancels=collections.defaultdict(list);terms=collections.defaultdict(list)
  for x in self.slot_history:
   if x.get('event')=='SLOT_CANCEL_REQUEST':cancels[str(x.get('key'))].append(x)
   elif x.get('event')=='SLOT_RELEASE':terms[str(x.get('key'))].append(x)
  for r in self.training_rows:
   t0=int(r['t']);key=r['key']
   for h in H:
    t1=t0+h*1000
    fs=[x for x in fills.get(key,[]) if t0<int(x['t'])<=t1]
    r[f'fillQty{h}s']=float(sum(float(x['confirmedQty']) for x in fs));r[f'anyFill{h}s']=1 if r[f'fillQty{h}s']>EPS else 0
    r[f'repairPayQty{h}s']=float(sum(float(x.get('matchedRepairQty') or 0) for x in fs));r[f'overflowQty{h}s']=float(sum(float(x.get('overflowQty') or 0) for x in fs))
    r[f'cancelReq{h}s']=1 if any(t0<int(x.get('t') or 0)<=t1 for x in cancels.get(key,[])) else 0
    r[f'terminal{h}s']=1 if any(t0<int(x.get('t') or 0)<=t1 for x in terms.get(key,[])) else 0
   o=self.orders.get(key) or {};r['finalCum']=float(o.get('cum') or 0.0);r['eventualFill']=1 if r['finalCum']>EPS else 0
  return self.training_rows

def role_code(r):
 return [1.0 if r['role']==x else 0.0 for x in ROLES]+[1.0 if r['route']==x else 0.0 for x in ROUTES]+[1.0 if r['side']=='UP' else 0.0]
STATE=['secondsLeft','floor','best','absNet','coverage','gross','debtUp','debtDown','totalDebt','targetDebtForActionSide','oldestRepairProgress','oldestRepairAgeMs','responsibilityCount','liveSlots','sideLiveSlots','repairLiveSlots','expandLiveSlots','pendingCancelCount','bookImbalance','spread','sideBid','sideAsk','sideMid','dominantMid','qLadderLive','qPendingActive','sideIsDominant','sideIsWeak']
ACTION=['price','qty','priceToBid','askToPrice','pairLegal','isRepairRole','isExpandRole']

def X(rows,with_action):
 a=np.asarray([[safe(r[k]) for k in STATE] for r in rows],dtype=np.float64)
 if not with_action:return a
 b=np.asarray([[safe(r[k]) for k in ACTION]+role_code(r) for r in rows],dtype=np.float64);return np.c_[a,b]

def auc(y,p):
 y=np.asarray(y,int)
 return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None

def ba(y,p):
 y=np.asarray(y,int)
 return float(balanced_accuracy_score(y,(np.asarray(p)>=.5).astype(int))) if len(np.unique(y))>1 else None

def fit_event(xtr,xva,ytr,yva,seed):
 if len(set(ytr))<2:return {'trainClasses':sorted(set(map(int,ytr)))} , None
 m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=18,l2_regularization=2.0,random_state=seed).fit(xtr,ytr)
 p=m.predict_proba(xva)[:,1];return {'n':len(yva),'positiveSupport':int(sum(yva)),'auc':auc(yva,p),'ba':ba(yva,p),'baseRate':float(np.mean(yva))},m

def fit_qty(xtr,xva,ytr,yva,seed):
 m=HistGradientBoostingRegressor(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=18,l2_regularization=2.0,random_state=seed).fit(xtr,ytr)
 p=np.maximum(0.0,m.predict(xva));sc=max(1.0,float(np.mean(np.abs(yva))))
 return {'mae':float(mean_absolute_error(yva,p)),'nmae':float(mean_absolute_error(yva,p)/sc),'actualMean':float(np.mean(yva)),'predMean':float(np.mean(p))},m

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);ap.add_argument('--train-markets',type=int,default=70);a=ap.parse_args()
 with tempfile.TemporaryDirectory(prefix='mgmt_v1_world_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   cohort=sorted(json.loads(z.read('cohort.json'))['rows'],key=lambda r:(int(r.get('windowEndMs') or 0),int(r['marketId'])))
   for cr in cohort:z.extract(f"tapes/{int(cr['marketId'])}.json.xz",root)
  rows=[]
  for i,cr in enumerate(cohort,1):
   mid=int(cr['marketId']);sim=TrainingTraceSim(root/'tapes'/f'{mid}.json.xz')
   try:r=sim.run_qty('__UNSCORED__');rr=sim.finalize_labels()
   finally:sim.close()
   for q in rr:q['marketId']=mid;q['windowEndMs']=int(cr.get('windowEndMs') or 0)
   rows+=rr
   print(json.dumps({'progress':i,'of':len(cohort),'marketId':mid,'rows':len(rr),'fills':int(r['fillEvents']),'ledger':(r.get('quantityLedgerSummary') or {}).get('invariantViolations') or {}},ensure_ascii=False),flush=True)
  mids=[int(x['marketId']) for x in cohort];cut=min(max(1,a.train_markets),len(mids)-1);trset=set(mids[:cut]);vaset=set(mids[cut:]);tr=[r for r in rows if r['marketId'] in trset];va=[r for r in rows if r['marketId'] in vaset]
  xs0,xv0=X(tr,False),X(va,False);xs1,xv1=X(tr,True),X(va,True)
  report={'version':'MANAGEMENT_TRAINING_V1_CURRENT_V3B_EXECUTION_WORLD','date':'2026-09-07','researchOnly':True,'actionAuthority':False,'bundle':str(a.bundle),'trainMarkets':len(trset),'validationMarkets':len(vaset),'trainRows':len(tr),'validationRows':len(va),'stateFeatures':STATE,'actionFeatures':ACTION+['roleOneHot','routeOneHot','sideUp'],'horizons':{},'roleStats':{},'guards':['current V3B exact-FIFO substrate','chronological market split','winner/Target future absent from features and labels','world/execution prediction only','no policy authority','NEW24-B untouched']}
  models={}
  for role in sorted(set(r['role'] for r in rows)):
   z=[r for r in va if r['role']==role];report['roleStats'][role]={'n':len(z),'fillRate3s':float(np.mean([r['anyFill3s'] for r in z])) if z else None,'fillRate5s':float(np.mean([r['anyFill5s'] for r in z])) if z else None,'meanFillQty5s':float(np.mean([r['fillQty5s'] for r in z])) if z else None}
  for h in H:
   hr={}
   for label in (f'anyFill{h}s',f'cancelReq{h}s',f'terminal{h}s'):
    yt=[int(r[label]) for r in tr];yv=[int(r[label]) for r in va]
    s0,m0=fit_event(xs0,xv0,yt,yv,100+h*10+len(hr));s1,m1=fit_event(xs1,xv1,yt,yv,200+h*10+len(hr));hr[label]={'stateOnly':s0,'stateAction':s1,'aucDeltaAction':None if s0.get('auc') is None or s1.get('auc') is None else float(s1['auc']-s0['auc']),'baDeltaAction':None if s0.get('ba') is None or s1.get('ba') is None else float(s1['ba']-s0['ba'])};models[(label,'stateAction')]=m1
   for label in (f'fillQty{h}s',f'repairPayQty{h}s',f'overflowQty{h}s'):
    yt=np.asarray([safe(r[label]) for r in tr]);yv=np.asarray([safe(r[label]) for r in va]);s0,m0=fit_qty(xs0,xv0,yt,yv,300+h*10+len(hr));s1,m1=fit_qty(xs1,xv1,yt,yv,400+h*10+len(hr));hr[label]={'stateOnly':s0,'stateAction':s1,'maeImprovementAction':float(s0['mae']-s1['mae'])};models[(label,'stateAction')]=m1
   report['horizons'][str(h)]=hr
  # Require that action context changes at least one held-out execution prediction metric; no promotion from this gate alone.
  deltas=[]
  for h in H:
   for k,v in report['horizons'][str(h)].items():
    if 'aucDeltaAction' in v and v['aucDeltaAction'] is not None:deltas.append(v['aucDeltaAction'])
    if 'maeImprovementAction' in v:deltas.append(v['maeImprovementAction'])
  report['trainingDiagnostic']={'maxPositiveActionIncrement':float(max(deltas)) if deltas else None,'positiveIncrementCount':int(sum(x>0 for x in deltas)),'metricCount':len(deltas),'decision':'WORLD_MODEL_ACTION_CONTEXT_EXERCISED' if any(x>0 for x in deltas) else 'ACTION_CONTEXT_NOT_IDENTIFIED'}
  out=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
  rows_out=out.parent/'training_rows.jsonl'
  with rows_out.open('w',encoding='utf-8') as f:
   for r in rows:f.write(json.dumps(r,ensure_ascii=False)+'\n')
  joblib.dump({'stateFeatures':STATE,'actionFeatures':ACTION,'models':models},out.parent/'world_models.joblib')
  print(json.dumps({'ok':True,'trainRows':len(tr),'validationRows':len(va),'diagnostic':report['trainingDiagnostic'],'horizons':report['horizons']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
