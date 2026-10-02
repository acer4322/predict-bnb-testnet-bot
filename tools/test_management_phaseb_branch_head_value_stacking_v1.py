from __future__ import annotations

"""Strict-forward stacking of a branch-aligned execution head into local action value.

Execution head is trained on Phase-B exact-fork branch resolution labels from earlier
markets only (structural fill + log resolution lag). Its predictions are then used as
causal features for a separate paired Repair-vs-Re-Expand local economic value model.
Future true resolution features are retained only as an ORACLE diagnostic upper bound.
"""
import argparse,json,math,os
from pathlib import Path
from typing import Any
import duckdb,numpy as np
from sklearn.ensemble import ExtraTreesClassifier,ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import roc_auc_score,mean_absolute_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

EPS=1e-9
BLOCKS=((20,40),(40,60),(60,80),(80,100))
OUTER=((20,40,40,60),(20,60,60,80),(20,80,80,100))
TARGETS=('dFloor','dBest','dFavoredPayoff','dWeakPayoff')
ROLES=('PROBE_CORE','ECONOMIC_CORE','SATELLITE_REPAIR','SATELLITE_EXPAND')
BRANCH_NUM=(
 'state_floor','state_best','state_abs_net','state_total_debt','state_initial_debt_qty','state_paid_debt_qty','state_remaining_debt_qty','state_repair_progress_frac','state_free_slots',
 'state_seconds_left','state_coverage','state_gross','state_debt_up','state_debt_down','state_oldest_repair_progress','state_oldest_repair_age_ms','state_responsibility_count','state_live_slots','state_repair_family_live_slots','state_satellite_expand_live_slots','state_expand_family_live_slots','state_pending_cancel_count','state_book_imbalance','state_spread','state_dominant_mid','state_q_ladder_live','state_q_pending_active',
 'context_side_bid','context_side_ask','context_side_mid','context_side_is_dominant','context_side_is_weak','context_target_debt_for_action_side',
 'action_price','action_qty','action_price_to_bid','action_ask_to_price','action_pair_legal','action_q_arm_used',
 'context_immediate_delta_floor','context_immediate_delta_best','context_immediate_delta_gap',
)
def safe(x:Any,d=0.0)->float:
 try:z=float(x);return z if math.isfinite(z) else d
 except:return d
def load(p:Path):
 c=duckdb.connect(database=':memory:')
 try:
  cur=c.execute("select * from read_parquet('"+p.resolve().as_posix().replace("'","''")+"') order by window_end_ms,market_id,decision_ms,action_class");cols=[x[0] for x in cur.description];return [dict(zip(cols,r)) for r in cur.fetchall()]
 finally:c.close()
def cat(r):
 role=str(r.get('action_role') or '');side=str(r.get('action_side') or '');cls=str(r.get('action_class') or '')
 return [1.0 if role==x else 0.0 for x in ROLES]+[1.0 if side=='UP' else 0.0,1.0 if cls=='REPAIR' else 0.0,1.0 if cls=='EXPAND' else 0.0]
def bx(rows):return np.asarray([[safe(r.get(k)) for k in BRANCH_NUM]+cat(r) for r in rows],float)
def auc(y,p):return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def fit_branch(tr,seed):
 x=bx(tr);y=np.asarray([int(r['label_structural_fill']) for r in tr],int);lag=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in tr],float))
 c=ExtraTreesClassifier(n_estimators=500,min_samples_leaf=3,max_features=.75,class_weight='balanced',random_state=seed,n_jobs=1).fit(x,y)
 g=ExtraTreesRegressor(n_estimators=500,min_samples_leaf=3,max_features=.75,random_state=seed+1,n_jobs=1).fit(x,lag)
 return c,g
def pred_branch(models,rows):
 c,g=models;x=bx(rows);p=c.predict_proba(x)[:,1];l=g.predict(x);return [{'pStructuralFill':float(p[i]),'predLogLag':float(l[i])} for i in range(len(rows))]
def current_payoffs(r):u,d,c=safe(r['state_up_qty']),safe(r['state_down_qty']),safe(r['state_cost']);return u-c,d-c
def immediate(r):
 u,d,c=safe(r['state_up_qty']),safe(r['state_down_qty']),safe(r['state_cost']);side=str(r['action_side']);p,q=safe(r['action_price']),safe(r['action_qty']);cu,cd=current_payoffs(r);dom=str(r.get('state_dominant_side') or '')
 if side=='UP':u+=q
 else:d+=q
 c+=p*q;pu,pd=u-c,d-c;fav=pu if dom=='UP' else pd if dom=='DOWN' else max(pu,pd);weak=pd if dom=='UP' else pu if dom=='DOWN' else min(pu,pd);cf=cu if dom=='UP' else cd if dom=='DOWN' else max(cu,cd);cw=cd if dom=='UP' else cu if dom=='DOWN' else min(cu,cd);fl,be=min(pu,pd),max(pu,pd)
 return {'dFloor':fl-safe(r['state_floor']),'dBest':be-safe(r['state_best']),'dGap':(be-fl)-(safe(r['state_best'])-safe(r['state_floor'])),'dFavored':fav-cf,'dWeak':weak-cw}
def make_pairs(rows,preds,mids):
 by={}
 for r in rows:by.setdefault(str(r['pair_id']),{})[str(r['action_class'])]=r
 out=[]
 for pid,g in by.items():
  if set(g)!={'REPAIR','EXPAND'}:continue
  rr,er=g['REPAIR'],g['EXPAND'];mid=int(rr['market_id']);idx=mids.index(mid)
  if idx<20:continue
  ri,ei=immediate(rr),immediate(er)
  rp=preds[(mid,int(rr['decision_ms']),'REPAIR')];ep=preds[(mid,int(er['decision_ms']),'EXPAND')]
  geom=[safe(rr['action_price']),safe(rr['action_qty']),safe(er['action_price']),safe(er['action_qty']),safe(rr['action_price_to_bid']),safe(er['action_price_to_bid']),ri['dFloor'],ei['dFloor'],ei['dFloor']-ri['dFloor'],ri['dBest'],ei['dBest'],ei['dBest']-ri['dBest'],ri['dFavored'],ei['dFavored'],ei['dFavored']-ri['dFavored'],ri['dWeak'],ei['dWeak'],ei['dWeak']-ri['dWeak']]
  ex=[rp['pStructuralFill'],ep['pStructuralFill'],ep['pStructuralFill']-rp['pStructuralFill'],rp['predLogLag'],ep['predLogLag'],ep['predLogLag']-rp['predLogLag']]
  rl=math.log1p(max(0.0,safe(rr['label_resolution_lag_ms'])));el=math.log1p(max(0.0,safe(er['label_resolution_lag_ms'])));oracle=[safe(rr['label_structural_fill']),safe(er['label_structural_fill']),safe(er['label_structural_fill'])-safe(rr['label_structural_fill']),rl,el,el-rl]
  tar={'dFloor':safe(er['label_resolution_delta_floor'])-safe(rr['label_resolution_delta_floor']),'dBest':safe(er['label_resolution_delta_best'])-safe(rr['label_resolution_delta_best']),'dFavoredPayoff':safe(er['label_resolution_delta_favored_payoff'])-safe(rr['label_resolution_delta_favored_payoff']),'dWeakPayoff':safe(er['label_resolution_delta_weak_payoff'])-safe(rr['label_resolution_delta_weak_payoff'])}
  mech={'dFloor':ei['dFloor']-ri['dFloor'],'dBest':ei['dBest']-ri['dBest'],'dFavoredPayoff':ei['dFavored']-ri['dFavored'],'dWeakPayoff':ei['dWeak']-ri['dWeak']}
  out.append({'marketId':mid,'t':int(rr['decision_ms']),'geom':geom,'exec':ex,'oracle':oracle,'targets':tar,'mechanical':mech})
 return sorted(out,key=lambda x:mids.index(x['marketId']))
def fit_value(kind,x,y,xx,seed):
 if kind=='EXTRATREES':m=ExtraTreesRegressor(n_estimators=400,min_samples_leaf=3,max_features=.75,random_state=seed,n_jobs=1)
 else:m=make_pipeline(StandardScaler(),Ridge(alpha=10.0))
 return m.fit(x,y).predict(xx)
def q(a,p):return float(np.quantile(np.asarray(a,float),p))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phaseb',required=True,type=Path);ap.add_argument('--output',required=True);a=ap.parse_args();rows=load(a.phaseb);mids=[]
 for r in rows:
  m=int(r['market_id'])
  if m not in mids:mids.append(m)
 if len(mids)!=100:raise RuntimeError('expected100 markets')
 preds={};brep=[]
 for bi,(st,en) in enumerate(BLOCKS,1):
  trm=set(mids[:st]);tem=set(mids[st:en]);tr=[r for r in rows if int(r['market_id']) in trm];te=[r for r in rows if int(r['market_id']) in tem];models=fit_branch(tr,260907+bi*10);pp=pred_branch(models,te);yy=np.asarray([int(r['label_structural_fill']) for r in te],int);lag=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in te],float));pa=np.asarray([x['pStructuralFill'] for x in pp]);pl=np.asarray([x['predLogLag'] for x in pp]);rep={'block':bi,'trainMarkets':st,'testMarkets':en-st,'structuralFillAuc':auc(yy,pa),'logLagMae':float(mean_absolute_error(lag,pl)),'naiveLagMae':float(mean_absolute_error(lag,np.full(len(lag),np.median(np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in tr],float))))))};brep.append(rep);print(json.dumps({'branchHead':rep},ensure_ascii=False),flush=True)
  for r,p in zip(te,pp):preds[(int(r['market_id']),int(r['decision_ms']),str(r['action_class']))]=p
 pairs=make_pairs(rows,preds,mids)
 if len(pairs)!=80:raise RuntimeError(f'expected80 pairs got{len(pairs)}')
 oos=[];frep=[]
 for fi,(a0,a1,b0,b1) in enumerate(OUTER,1):
  trm=set(mids[a0:a1]);tem=set(mids[b0:b1]);tr=[r for r in pairs if r['marketId'] in trm];te=[r for r in pairs if r['marketId'] in tem];xg=np.asarray([r['geom'] for r in tr],float);xgg=np.asarray([r['geom'] for r in te],float);xe=np.asarray([r['geom']+r['exec'] for r in tr],float);xee=np.asarray([r['geom']+r['exec'] for r in te],float);xo=np.asarray([r['geom']+r['oracle'] for r in tr],float);xoo=np.asarray([r['geom']+r['oracle'] for r in te],float);pr={t:{} for t in TARGETS};sc={}
  for ti,t in enumerate(TARGETS):
   y=np.asarray([r['targets'][t] for r in tr],float);yy=np.asarray([r['targets'][t] for r in te],float);sc[t]=max(float(np.std(y)),.25);pr[t]['true']=yy;pr[t]['MECHANICAL']=np.asarray([r['mechanical'][t] for r in te],float)
   for k in ('EXTRATREES','RIDGE'):
    pr[t][k+'_GEOM']=fit_value(k,xg,y,xgg,260000+fi*100+ti);pr[t][k+'_EXEC']=fit_value(k,xe,y,xee,261000+fi*100+ti);pr[t][k+'_ORACLE']=fit_value(k,xo,y,xoo,262000+fi*100+ti)
  fr={'fold':fi,'trainMarkets':len(trm),'testMarkets':len(tem),'mae':{}}
  for t in TARGETS:fr['mae'][t]={k:float(mean_absolute_error(pr[t]['true'],v)) for k,v in pr[t].items() if k!='true'}
  frep.append(fr)
  for j,r in enumerate(te):
   z={'fold':fi,'marketId':r['marketId'],'vectorError':{}}
   for model in ('MECHANICAL','EXTRATREES_GEOM','EXTRATREES_EXEC','EXTRATREES_ORACLE','RIDGE_GEOM','RIDGE_EXEC','RIDGE_ORACLE'):z['vectorError'][model]=float(np.mean([abs(float(pr[t][model][j])-float(pr[t]['true'][j]))/sc[t] for t in TARGETS]))
   oos.append(z)
 def gate(new,base):
  ne=np.asarray([x['vectorError'][new] for x in oos]);ba=np.asarray([x['vectorError'][base] for x in oos]);d=ba-ne;im=int(np.sum(d>EPS));wo=int(np.sum(d<-EPS));return {'comparison':new+'_vs_'+base,'improvedMarkets':im,'worsenedMarkets':wo,'tiedMarkets':len(d)-im-wo,'improvedMarketRate':im/len(d),'meanVectorErrorImprovement':float(np.mean(d)),'p10PerMarketImprovement':q(d,.1),'aggregateErrorImproves':bool(np.mean(d)>0),'seventyPercentGate':bool(im/len(d)>=.70),'tailGuard':bool(q(ne,.9)<=1.10*q(ba,.9)+EPS)}
 primary=gate('EXTRATREES_EXEC','EXTRATREES_GEOM');primary['developmentGatePass']=bool(primary['seventyPercentGate'] and primary['aggregateErrorImproves'] and primary['tailGuard']);oracle=gate('EXTRATREES_ORACLE','EXTRATREES_GEOM');ridge=gate('RIDGE_EXEC','RIDGE_GEOM')
 verdict='BRANCH_ALIGNED_EXECUTION_VALUE_ESTABLISHED' if primary['developmentGatePass'] else ('BRANCH_HEAD_PARTIAL_VALUE_SIGNAL' if primary['aggregateErrorImproves'] and primary['improvedMarketRate']>=.60 else 'BRANCH_HEAD_NOT_SUFFICIENT_FOR_LOCAL_VALUE')
 out={'version':'MANAGEMENT_PHASEB_BRANCH_HEAD_VALUE_STACKING_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'branchHeadForwardBlocks':brep,'pairCount':len(pairs),'valueFoldDefinition':'strict forward branch-head blocks 20->20; paired value train 20:40/test40:60,20:60/test60:80,20:80/test80:100','folds':frep,'primaryGate':primary,'oracleDiagnostic':oracle,'ridgeRobustness':ridge,'verdict':verdict,'boundary':['branch head trained only on earlier Phase-B markets','structural fill + resolution lag only','paired local value model trained only on earlier markets','future true resolution used only in oracle diagnostic','no winner/Target/future causal feature','fixed model families; no tuning','70/30 development analogue only','no policy authority/no NEW24-B/no dream fill/no 8781']}
 op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'verdict':verdict,'primary':primary,'oracle':oracle,'ridge':ridge,'branchHead':brep},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
