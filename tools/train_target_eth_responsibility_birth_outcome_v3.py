from __future__ import annotations
import argparse,collections,json,math,os,sqlite3,statistics
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

EPS=1e-9;SEED=20260904;SIDES=('UP','DOWN')
FEATURES=['seconds_left_norm','expand_price','expand_qty_log','expand_qty_gross_ratio','route_maker_frac','same_clock_after_repair','same_clock_direct_pair','pair_coverage','absnet_ratio','floor_ratio','best_ratio','gross_log','recent30_birth_frac','recent30_repair_frac','recent30_direct_pair_frac','recent30_maker_qty_frac','prior_completion_age_log','prior_completed_pair_sum','prior_completed_duration_log','prior_completed_payment_count_norm','event_count_norm']
TASKS=['COMPLETE_WITHIN_60S','NON_DAMAGING_IF_COMPLETED','GOOD_RESPONSIBILITY']

def opp(s):return 'DOWN' if s=='UP' else 'UP'
def slog(x,cap=120000.):return math.log1p(min(max(float(x),0.),cap))/math.log1p(cap)
def met(up,dn,cost):
 g=up+dn;p=min(up,dn);gap=abs(up-dn);return {'gross':g,'pc':2*p/g if g>EPS else 1.,'ab':gap/g if g>EPS else 0.,'floor':p-cost,'best':max(up,dn)-cost}

def fit(X,y):
 y=np.asarray(y,int);p=max(float(y.mean()),1e-6);w=np.where(y==1,.5/p,.5/max(1-p,1e-6));m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.0,random_state=SEED,early_stopping=False);m.fit(X,y,sample_weight=w);return m

def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int);return {'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if len(y) else None}

def build(db):
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row;tables={r[0] for r in c.execute("select name from sqlite_master where type='table'")}
 if 'eth_events' in tables:events=list(c.execute('select id,market_id,role,side,event_ms,price,shares from eth_events order by market_id,event_ms,id'))
 else:events=list(c.execute("select id,market_id,role,side,event_ms,price,shares from wallet_shadow_target_events where asset='ETH' order by market_id,event_ms,id"))
 if 'eth_markets' in tables:mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute('select market_id,window_end_ms from eth_markets where window_end_ms is not null')}
 else:mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
 c.close();by=collections.defaultdict(lambda:collections.defaultdict(list))
 for r in events:by[int(r['market_id'])][int(r['event_ms'])].append(r)
 ends=sorted({mend[mid] for mid in by if mid in mend});cut1=ends[int(len(ends)*.60)];cut2=ends[int(len(ends)*.80)];lots=[];lot_id=0
 for mi,(mid,clocks) in enumerate(sorted(by.items()),1):
  end=mend.get(mid);up=dn=cost=0.;queues={'UP':collections.deque(),'DOWN':collections.deque()};recent=collections.deque();last_completed=None;clock_n=0
  for t,legs in sorted(clocks.items()):
   clock_n+=1
   while recent and t-recent[0]['t']>30000:recent.popleft()
   agg={s:{'q':0.,'notional':0.,'maker':0.,'taker':0.} for s in SIDES}
   for r in legs:
    s=str(r['side']).upper();q=float(r['shares']);px=float(r['price']);rr=str(r['role']).upper()
    if s not in SIDES or q<=EPS:continue
    agg[s]['q']+=q;agg[s]['notional']+=q*px
    if rr=='MAKER':agg[s]['maker']+=q
    elif rr=='TAKER':agg[s]['taker']+=q
   for s in SIDES:agg[s]['px']=agg[s]['notional']/agg[s]['q'] if agg[s]['q']>EPS else None
   rem={s:agg[s]['q'] for s in SIDES};repair_qty=0.;completed_now=[]
   for pay_side in SIDES:
    dq=queues[opp(pay_side)];need=rem[pay_side]
    while need>EPS and dq:
     lot=dq[0];take=min(need,float(lot['remaining']));lot['remaining']-=take;lot['paidQty']+=take;lot['pairCostSum']+=take*(float(lot['expandPrice'])+float(agg[pay_side]['px']));lot['paymentClocks'].add(t);repair_qty+=take;need-=take
     if lot['remaining']<=EPS:
      done=dq.popleft();done['completedAt']=t;done['durationMs']=t-int(done['bornAt']);done['paymentCount']=len(done['paymentClocks']);completed_now.append(done);last_completed=done
     if take<=EPS:break
    rem[pay_side]=need
   direct=min(rem['UP'],rem['DOWN']);rem['UP']-=direct;rem['DOWN']-=direct
   up+=agg['UP']['q'];dn+=agg['DOWN']['q'];cost+=agg['UP']['notional']+agg['DOWN']['notional'];m=met(up,dn,cost);den=max(abs(cost),1.)
   tot_comp=sum(x['birthQty']+x['repairQty']+x['directQty'] for x in recent);birth_comp=sum(x['birthQty'] for x in recent);repair_comp=sum(x['repairQty'] for x in recent);direct_comp=sum(x['directQty'] for x in recent);maker_recent=sum(x['makerQty'] for x in recent);route_recent=sum(x['routeQty'] for x in recent)
   prior=last_completed
   birth_total=0.
   for s in SIDES:
    q=rem[s]
    if q<=EPS:continue
    lot_id+=1;birth_total+=q;maker_frac=agg[s]['maker']/agg[s]['q'] if agg[s]['q']>EPS else 0.;sl=0. if end is None else max(-30.,min(330.,(end-t)/1000.))/300.;prior_age=1e6 if prior is None else t-int(prior['completedAt']);prior_pair=1.0 if prior is None or prior['paidQty']<=EPS else float(prior['pairCostSum'])/float(prior['paidQty']);prior_dur=1e6 if prior is None else float(prior['durationMs']);prior_pay=0. if prior is None else min(float(prior['paymentCount']),12.)/12.
    x=[sl,float(agg[s]['px']),math.log1p(q)/math.log1p(100.),q/max(m['gross'],EPS),maker_frac,float(repair_qty>EPS),float(direct>EPS),m['pc'],m['ab'],max(-5.,min(5.,m['floor']/den)),max(-5.,min(5.,m['best']/den)),math.log1p(m['gross'])/math.log1p(500.),birth_comp/max(tot_comp,EPS) if tot_comp>EPS else 0.,repair_comp/max(tot_comp,EPS) if tot_comp>EPS else 0.,direct_comp/max(tot_comp,EPS) if tot_comp>EPS else 0.,maker_recent/max(route_recent,EPS) if route_recent>EPS else 0.,slog(prior_age),max(0.,min(2.,prior_pair)),slog(prior_dur),prior_pay,math.log1p(clock_n)/math.log1p(128.)]
    lot={'id':lot_id,'market':mid,'end':end,'bornAt':t,'side':s,'expandPrice':float(agg[s]['px']),'initialQty':q,'remaining':q,'paidQty':0.,'pairCostSum':0.,'paymentClocks':set(),'completedAt':None,'durationMs':None,'paymentCount':0,'x':np.asarray(x,np.float32),'sameClockAfterRepair':repair_qty>EPS};queues[s].append(lot);lots.append(lot)
   recent.append({'t':t,'birthQty':birth_total,'repairQty':repair_qty,'directQty':direct,'makerQty':agg['UP']['maker']+agg['DOWN']['maker'],'routeQty':agg['UP']['q']+agg['DOWN']['q']})
  if mi%500==0:print(json.dumps({'progressMarkets':mi,'of':len(by)}),flush=True)
 # labels after all markets complete
 for z in lots:
  comp=z['completedAt'] is not None;pair=(z['pairCostSum']/z['paidQty']) if comp and z['paidQty']>EPS else None;z['pairAvg']=pair;z['COMPLETE_WITHIN_60S']=int(comp and int(z['completedAt'])-int(z['bornAt'])<=60000);z['NON_DAMAGING_IF_COMPLETED']=None if not comp else int(pair<=1.0+EPS);z['GOOD_RESPONSIBILITY']=int(comp and pair<=1.0+EPS);e=int(z['end'] or 0);z['split']='train' if e<cut1 else 'validation' if e<cut2 else 'test'
 return lots,ends,cut1,cut2

def task_rows(rows,task):return [r for r in rows if task!='NON_DAMAGING_IF_COMPLETED' or r[task] is not None]
def arr(z,task):return np.stack([r['x'] for r in z]),np.asarray([int(r[task]) for r in z],int)
def eval_task(rows,ends,task):
 tr=[r for r in task_rows(rows,task) if r['split']=='train'];va=[r for r in task_rows(rows,task) if r['split']=='validation'];te=[r for r in task_rows(rows,task) if r['split']=='test'];X,y=arr(tr,task);m=fit(X,y);out={}
 for name,z in [('validation',va),('test',te)]:Xt,yt=arr(z,task);out[name]=metric(yt,m.predict_proba(Xt)[:,1])
 rolls=[];rz=task_rows(rows,task)
 for k,(a,b) in enumerate([(.5,.6),(.6,.7),(.7,.8),(.8,1.)],1):
  c1=ends[min(len(ends)-1,int(len(ends)*a))];c2=ends[min(len(ends)-1,int(len(ends)*b)-1)] if b<1 else ends[-1];rtr=[r for r in rz if int(r['end'] or 0)<c1];rte=[r for r in rz if int(r['end'] or 0)>=c1 and int(r['end'] or 0)<=c2]
  if len(rtr)<500 or len(rte)<200:rolls.append({'fold':k,'trainN':len(rtr),'testN':len(rte),'auc':None});continue
  X,y=arr(rtr,task);Xt,yt=arr(rte,task);mm=fit(X,y);rolls.append({'fold':k,'trainN':len(rtr),'testN':len(rte),**metric(yt,mm.predict_proba(Xt)[:,1])})
 aucs=[x['auc'] for x in rolls if x.get('auc') is not None];out['rolling']=rolls;out['rollingMedianAuc']=float(statistics.median(aucs)) if aucs else None;out['rollingMinAuc']=min(aucs) if aucs else None
 fin=[r for r in task_rows(rows,task) if r['split'] in ('train','validation')];X,y=arr(fin,task);out['finalModel']=fit(X,y);out['finalTrainN']=len(fin);return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args();rows,ends,c1,c2=build(a.db);res={};models={};gates={}
 for task in TASKS:
  r=eval_task(rows,ends,task);models[task]=r.pop('finalModel');res[task]=r;gates[task]={'minimumRows':sum(1 for x in task_rows(rows,task))>=1000,'testAuc':(r['test']['auc'] or 0)>=.60,'rollingMedianAuc':(r['rollingMedianAuc'] or 0)>=.60,'rollingMinAuc':(r['rollingMinAuc'] or 0)>=.55}
 pair=[r['pairAvg'] for r in rows if r.get('pairAvg') is not None];dur=[int(r['completedAt'])-int(r['bornAt']) for r in rows if r.get('completedAt') is not None];same=sum(bool(r['sameClockAfterRepair']) for r in rows)
 decision='KEEP_V3_BIRTH_OUTCOME_HEADS_AS_SHADOW' if all(all(v.values()) for v in gates.values()) else 'PARTIAL_KEEP_V3_BIRTH_OUTCOME_SHADOW'
 out={'version':'TARGET_ETH_RESPONSIBILITY_BIRTH_OUTCOME_V3','date':'2026-09-04','researchOnly':True,'actionAuthority':False,'sourceDb':os.path.abspath(a.db),'birthRows':len(rows),'sameClockAfterRepairBirths':same,'sameClockAfterRepairShare':same/max(1,len(rows)),'completedRows':sum(r.get('completedAt') is not None for r in rows),'completedPairAvgMedian':statistics.median(pair) if pair else None,'completedDurationMsMedian':statistics.median(dur) if dur else None,'features':FEATURES,'splitCutoffs':{'trainEndExclusiveMs':c1,'validationEndExclusiveMs':c2},'results':res,'gates':gates,'decision':decision,'boundary':['V3 single-use FIFO responsibility labels','strict-current birth features only','future Repair only labels outcomes','no winner/PnL feature','no threshold sweep','shadow only','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'features':FEATURES,'models':models,'tasks':TASKS,'actionAuthority':False,'boundary':out['boundary']},a.model_out);print(json.dumps({'ok':True,'decision':decision,'birthRows':len(rows),'sameClockAfterRepairShare':out['sameClockAfterRepairShare'],'summary':{t:{'testAuc':res[t]['test']['auc'],'rollingMedian':res[t]['rollingMedianAuc'],'rollingMin':res[t]['rollingMinAuc'],'testN':res[t]['test']['n']} for t in TASKS},'gates':gates},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
