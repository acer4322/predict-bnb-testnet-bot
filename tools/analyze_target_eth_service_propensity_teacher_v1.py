from __future__ import annotations
import argparse,collections,json,math,sqlite3,time
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder,StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss

EPS=1e-9;SIDES=('UP','DOWN')
def opp(s):return 'DOWN' if s=='UP' else 'UP'
NUM=['logQty','logAge','lotCount','oldestShare','priorNoRepairClocks','logSinceRepair','sinceRepairMissing']
CAT=['priorTransition']
def build_rows(db):
 c=sqlite3.connect(f'file:{db}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 ev=list(c.execute("select id,market_id,role,side,event_ms,price,shares from wallet_shadow_target_events where asset='ETH' order by market_id,event_ms,id"));c.close()
 by=collections.defaultdict(lambda:collections.defaultdict(list))
 for r in ev:by[int(r['market_id'])][int(r['event_ms'])].append(r)
 rows=[];viol=collections.Counter();lot_id=0;market_first={}
 for mid,clocks in sorted(by.items()):
  market_first[mid]=min(clocks);qs={s:collections.deque() for s in SIDES};out={s:0.0 for s in SIDES};hold={s:0.0 for s in SIDES};prior_no_rep=0;last_rep=None;last_tr='NONE'
  for t,legs in sorted(clocks.items()):
   pre=dict(out);pre_total=pre['UP']+pre['DOWN'];pre_lots=len(qs['UP'])+len(qs['DOWN']);pre_side='UP' if pre['UP']>EPS else ('DOWN' if pre['DOWN']>EPS else None);oldest=qs[pre_side][0] if pre_side else None
   age=t-int(oldest['bornAt']) if oldest else 0;oldrem=float(oldest['remaining']) if oldest else 0.0;oshare=oldrem/pre_total if pre_total>EPS else 0.0
   since=(t-last_rep) if last_rep is not None else None
   agg={s:{'q':0.0,'notional':0.0} for s in SIDES}
   for r in legs:
    s=str(r['side']).upper();q=float(r['shares']);p=float(r['price'])
    if s not in SIDES or q<=EPS:continue
    agg[s]['q']+=q;agg[s]['notional']+=q*p
   rem={s:agg[s]['q'] for s in SIDES};repair={s:0.0 for s in SIDES}
   for pay in SIDES:
    dq=qs[opp(pay)];need=rem[pay]
    while need>EPS and dq:
     lot=dq[0];take=min(need,float(lot['remaining']))
     if take<=EPS:break
     lot['remaining']-=take;out[opp(pay)]-=take;repair[pay]+=take;need-=take
     if lot['remaining']<=EPS:dq.popleft()
    rem[pay]=need
   pair_now=min(rem['UP'],rem['DOWN'])
   if pair_now>EPS:rem['UP']-=pair_now;rem['DOWN']-=pair_now
   birth=0.0
   for s in SIDES:
    q=rem[s]
    if q<=EPS:continue
    lot_id+=1;qs[s].append({'id':lot_id,'side':s,'bornAt':t,'remaining':q});out[s]+=q;birth+=q
   rep=repair['UP']+repair['DOWN'];post=out['UP']+out['DOWN']
   if pre_total>EPS and (rep>EPS or birth>EPS):
    if rep>EPS and birth>EPS:tr='REPAIR_PLUS_EXPAND'
    elif rep>EPS:tr='REPAIR_PRESENT_NO_NEW_EXPAND'
    else:tr='EXPAND_ONLY_WITH_DEBT'
    rows.append({'marketId':mid,'t':t,'logQty':math.log1p(pre_total),'logAge':math.log1p(max(0,age)/1000.0),'lotCount':float(pre_lots),'oldestShare':float(oshare),'priorNoRepairClocks':float(prior_no_rep),
                 'logSinceRepair':math.log1p(max(0,since)/1000.0) if since is not None else 0.0,'sinceRepairMissing':1.0 if since is None else 0.0,'priorTransition':last_tr,
                 'service':1 if rep>EPS else 0,'composite':1 if (rep>EPS and birth>EPS) else 0})
    if rep>EPS:prior_no_rep=0;last_rep=t
    else:prior_no_rep+=1
    last_tr=tr
   else:
    if pre_total<=EPS:prior_no_rep=0;last_tr='NONE'
   hold['UP']+=agg['UP']['q'];hold['DOWN']+=agg['DOWN']['q'];gap=abs(hold['UP']-hold['DOWN'])
   if abs(post-gap)>1e-6:viol['outstandingGapMismatch']+=1
   if out['UP']>EPS and out['DOWN']>EPS:viol['twoOutstandingSides']+=1
 return rows,market_first,dict(viol)
def split_markets(market_first):
 mids=[m for m,_ in sorted(market_first.items(),key=lambda kv:(kv[1],kv[0]))];n=len(mids);a=int(.70*n);b=int(.85*n);return set(mids[:a]),set(mids[a:b]),set(mids[b:])
def X(rows):return pd.DataFrame([{k:r[k] for k in NUM+CAT} for r in rows])
def make_pipe(num=NUM,cat=CAT):
 pre=ColumnTransformer([('num',StandardScaler(),num),('cat',OneHotEncoder(handle_unknown='ignore'),cat)],remainder='drop')
 return Pipeline([('pre',pre),('lr',LogisticRegression(max_iter=1000,solver='lbfgs'))])
def metrics(model,rows,label):
 y=np.array([r[label] for r in rows],dtype=int);p=model.predict_proba(X(rows))[:,1]
 out={'n':len(rows),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p)),'brier':float(brier_score_loss(y,p))}
 qs=np.quantile(p,np.linspace(0,1,11));cal=[]
 for i in range(10):
  lo,hi=qs[i],qs[i+1];mask=(p>=lo)&((p<=hi) if i==9 else (p<hi))
  if mask.sum():cal.append({'bin':i,'n':int(mask.sum()),'pMean':float(p[mask].mean()),'yRate':float(y[mask].mean()),'pMin':float(p[mask].min()),'pMax':float(p[mask].max())})
 out['calibrationDeciles']=cal;return out
def coeffs(model):
 pre=model.named_steps['pre'];names=list(pre.get_feature_names_out());co=model.named_steps['lr'].coef_[0];return sorted(({'feature':n,'coefficient':float(c)} for n,c in zip(names,co)),key=lambda z:abs(z['coefficient']),reverse=True)
def fit_eval(train,val,test,label):
 m=make_pipe();m.fit(X(train),[r[label] for r in train]);return m,{'train':metrics(m,train,label),'validation':metrics(m,val,label),'test':metrics(m,test,label),'coefficients':coeffs(m)}
def ablations(train,test,label):
 specs=[('full',NUM,CAT),('no_qty',[x for x in NUM if x!='logQty'],CAT),('no_age',[x for x in NUM if x!='logAge'],CAT),('no_lot_count',[x for x in NUM if x!='lotCount'],CAT),('no_oldest_share',[x for x in NUM if x!='oldestShare'],CAT),('no_wait_progress',[x for x in NUM if x!='priorNoRepairClocks'],CAT),('no_prior_transition',NUM,[]),('qty_only',['logQty'],[]),('topology_only',['logQty','lotCount','oldestShare'],[])]
 out=[]
 for name,num,cat in specs:
  m=make_pipe(num,cat);xt=pd.DataFrame([{k:r[k] for k in num+cat} for r in train]);xe=pd.DataFrame([{k:r[k] for k in num+cat} for r in test]);m.fit(xt,[r[label] for r in train]);y=np.array([r[label] for r in test]);p=m.predict_proba(xe)[:,1];out.append({'name':name,'testAuc':float(roc_auc_score(y,p)),'testLogLoss':float(log_loss(y,p))})
 return out
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',default='data/target_wallet_official_v1.db');ap.add_argument('--output',required=True);a=ap.parse_args();ts=time.time();rows,mf,viol=build_rows(a.db);trm,vam,tem=split_markets(mf)
 train=[r for r in rows if r['marketId'] in trm];val=[r for r in rows if r['marketId'] in vam];test=[r for r in rows if r['marketId'] in tem]
 svc_model,svc=fit_eval(train,val,test,'service');rep_train=[r for r in train if r['service']];rep_val=[r for r in val if r['service']];rep_test=[r for r in test if r['service']];cmp_model,cmp=fit_eval(rep_train,rep_val,rep_test,'composite')
 out={'version':'TARGET_ETH_SERVICE_PROPENSITY_TEACHER_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'sourceDb':a.db,'runtimeSeconds':time.time()-ts,
      'split':{'markets':{'train':len(trm),'validation':len(vam),'test':len(tem)},'rows':{'train':len(train),'validation':len(val),'test':len(test)},'chronologicalMarketLevel':True},
      'serviceNowModel':svc,'compositeGivenRepairModel':cmp,'serviceAblations':ablations(train,test,'service'),'compositeAblations':ablations(rep_train,rep_test,'composite'),'invariantViolations':viol,
      'boundary':['strict-past responsibility topology only','market-level chronological split','fixed logistic regression; no threshold tuning','no winner/PnL/future features','teacher diagnostic only; no runtime authority','FIFO reconstruction hypothesis']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'split':out['split'],'serviceTest':svc['test'],'compositeTest':cmp['test'],'serviceAblations':out['serviceAblations'],'compositeAblations':out['compositeAblations'],'serviceTopCoefficients':svc['coefficients'][:12],'compositeTopCoefficients':cmp['coefficients'][:12],'invariantViolations':viol},ensure_ascii=False))
if __name__=='__main__':main()
