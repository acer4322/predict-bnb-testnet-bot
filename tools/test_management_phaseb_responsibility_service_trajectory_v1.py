from __future__ import annotations

"""Strict-forward test of event-clock responsibility/service trajectory value.

This is deliberately not a fixed-seconds strategy rule. Trajectory is indexed by
prior OUR decision events and exact-FIFO debt/state evolution before the Phase-B
fork. Current native action at the fork is excluded. A shuffled-trajectory control
checks whether gains are real temporal information or just extra dimensions.
"""
import argparse,json,math,os
from pathlib import Path
from typing import Any
import duckdb,numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

EPS=1e-9
FOLDS=((0,40,40,60),(0,60,60,80),(0,80,80,100))
TARGETS=('dFloor','dBest','dFavoredPayoff','dWeakPayoff')
LAGS=(1,2,4,8);WINDOWS=(4,8)
SNAP=(
 'state_initial_debt_qty','state_paid_debt_qty','state_remaining_debt_qty','state_repair_progress_frac','state_total_debt',
 'state_responsibility_count','state_q_ladder_live','state_q_pending_active','state_repair_family_live_slots','state_expand_family_live_slots',
 'state_free_slots','state_floor','state_best','state_abs_net','state_coverage','state_gross','state_book_imbalance','state_spread',
)
def safe(x:Any,d=0.0)->float:
 try:z=float(x);return z if math.isfinite(z) else d
 except:return d
def load(p:Path):
 c=duckdb.connect(database=':memory:')
 try:
  cur=c.execute("select * from read_parquet('"+p.resolve().as_posix().replace("'","''")+"') order by window_end_ms,market_id,decision_ms");cols=[x[0] for x in cur.description];return [dict(zip(cols,r)) for r in cur.fetchall()]
 finally:c.close()
def curpay(r):u,d,c=safe(r['state_up_qty']),safe(r['state_down_qty']),safe(r['state_cost']);return u-c,d-c
def immediate(r):
 u,d,c=safe(r['state_up_qty']),safe(r['state_down_qty']),safe(r['state_cost']);side=str(r['action_side']);p,q=safe(r['action_price']),safe(r['action_qty']);cu,cd=curpay(r);dom=str(r.get('state_dominant_side') or '')
 if side=='UP':u+=q
 else:d+=q
 c+=p*q;pu,pd=u-c,d-c;fav=pu if dom=='UP' else pd if dom=='DOWN' else max(pu,pd);weak=pd if dom=='UP' else pu if dom=='DOWN' else min(pu,pd);cf=cu if dom=='UP' else cd if dom=='DOWN' else max(cu,cd);cw=cd if dom=='UP' else cu if dom=='DOWN' else min(cu,cd);fl,be=min(pu,pd),max(pu,pd)
 return {'dFloor':fl-safe(r['state_floor']),'dBest':be-safe(r['state_best']),'dGap':(be-fl)-(safe(r['state_best'])-safe(r['state_floor'])),'dFavored':fav-cf,'dWeak':weak-cw}
def geom_pair(rr,er):
 ri,ei=immediate(rr),immediate(er)
 return [safe(rr['action_price']),safe(rr['action_qty']),safe(er['action_price']),safe(er['action_qty']),safe(rr['action_price_to_bid']),safe(er['action_price_to_bid']),ri['dFloor'],ei['dFloor'],ei['dFloor']-ri['dFloor'],ri['dBest'],ei['dBest'],ei['dBest']-ri['dBest'],ri['dFavored'],ei['dFavored'],ei['dFavored']-ri['dFavored'],ri['dWeak'],ei['dWeak'],ei['dWeak']-ri['dWeak']],ri,ei
def action_family(r):
 if safe(r.get('action_is_repair_role'))>0.5:return 'REPAIR'
 if safe(r.get('action_is_expand_role'))>0.5:return 'EXPAND'
 if str(r.get('action_role') or '')=='PROBE_CORE':return 'PROBE'
 return 'OTHER'
def trajectory(history,current):
 # history contains only decision_ms < current fork time. Current row contributes state only.
 seq=history+[current];f=[]
 fields=('state_total_debt','state_floor','state_best','state_abs_net','state_responsibility_count','state_book_imbalance','state_dominant_mid','state_live_slots')
 for lag in LAGS:
  has=len(seq)>lag;f.append(1.0 if has else 0.0)
  if has:
   old=seq[-1-lag];f.append(math.log1p(max(0,int(current['decision_ms'])-int(old['decision_ms']))))
   for k in fields:f.append(safe(current.get(k))-safe(old.get(k)))
  else:f += [0.0]*(1+len(fields))
 for n in WINDOWS:
  ss=seq[-(n+1):];service=birth=0.0;sc=bc=fu=fd=flip=0
  for a,b in zip(ss[:-1],ss[1:]):
   dd=safe(b.get('state_total_debt'))-safe(a.get('state_total_debt'))
   if dd<-EPS:service+=-dd;sc+=1
   elif dd>EPS:birth+=dd;bc+=1
   df=safe(b.get('state_floor'))-safe(a.get('state_floor'))
   if df>EPS:fu+=1
   elif df<-EPS:fd+=1
   da=str(a.get('state_dominant_side') or '');db=str(b.get('state_dominant_side') or '')
   if da in ('UP','DOWN') and db in ('UP','DOWN') and da!=db:flip+=1
  prior=history[-n:];rep=sum(action_family(x)=='REPAIR' for x in prior);exp=sum(action_family(x)=='EXPAND' for x in prior);probe=sum(action_family(x)=='PROBE' for x in prior);active=sum(str(x.get('action_route') or '')=='ACTIVE' for x in prior);up=sum(str(x.get('action_side') or '')=='UP' for x in prior)
  f += [service,birth,service-birth,float(sc),float(bc),float(fu),float(fd),float(flip),float(rep),float(exp),float(probe),float(active),float(up)/len(prior) if prior else 0.0]
 dom=str(current.get('state_dominant_side') or '');run=1
 for x in reversed(history):
  if str(x.get('state_dominant_side') or '')==dom:run+=1
  else:break
 f.append(float(run))
 if history:
  fam=action_family(history[-1]);arun=1
  for x in reversed(history[:-1]):
   if action_family(x)==fam:arun+=1
   else:break
 else:fam='NONE';arun=0
 f += [float(arun),1.0 if fam=='REPAIR' else 0.0,1.0 if fam=='EXPAND' else 0.0,1.0 if fam=='PROBE' else 0.0]
 return f
def make_pairs(our,phaseb):
 hist={}
 for r in our:hist.setdefault(int(r['market_id']),[]).append(r)
 for v in hist.values():v.sort(key=lambda x:int(x['decision_ms']))
 pg={}
 for r in phaseb:pg.setdefault(str(r['pair_id']),{})[str(r['action_class'])]=r
 out=[]
 for pid,g in pg.items():
  if set(g)!={'REPAIR','EXPAND'}:continue
  rr,er=g['REPAIR'],g['EXPAND'];mid=int(rr['market_id']);t=int(rr['decision_ms']);matches=[x for x in hist[mid] if int(x['decision_ms'])==t]
  if len(matches)!=1:raise RuntimeError(f'OUR match {mid}/{t}={len(matches)}')
  current=matches[0];prior=[x for x in hist[mid] if int(x['decision_ms'])<t];gv,ri,ei=geom_pair(rr,er);snap=[safe(rr.get(k)) for k in SNAP];tr=trajectory(prior,current)
  tar={'dFloor':safe(er['label_terminal_floor'])-safe(rr['label_terminal_floor']),'dBest':safe(er['label_terminal_best'])-safe(rr['label_terminal_best']),'dFavoredPayoff':safe(er['label_terminal_favored_payoff'])-safe(rr['label_terminal_favored_payoff']),'dWeakPayoff':safe(er['label_terminal_weak_payoff'])-safe(rr['label_terminal_weak_payoff'])};mech={'dFloor':ei['dFloor']-ri['dFloor'],'dBest':ei['dBest']-ri['dBest'],'dFavoredPayoff':ei['dFavored']-ri['dFavored'],'dWeakPayoff':ei['dWeak']-ri['dWeak']}
  out.append({'marketId':mid,'t':t,'geom':gv,'snap':snap,'traj':tr,'targets':tar,'mechanical':mech})
 return sorted(out,key=lambda x:x['marketId'])
def fit(kind,x,y,xx,seed):
 if kind=='EXTRATREES':m=ExtraTreesRegressor(n_estimators=400,min_samples_leaf=3,max_features=.75,random_state=seed,n_jobs=1)
 else:m=make_pipeline(StandardScaler(),Ridge(alpha=10.0))
 return m.fit(x,y).predict(xx)
def q(a,p):return float(np.quantile(np.asarray(a,float),p))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--our',required=True,type=Path);ap.add_argument('--phaseb',required=True,type=Path);ap.add_argument('--output',required=True);a=ap.parse_args();our=load(a.our);phaseb=load(a.phaseb);pairs=make_pairs(our,phaseb);mids=[r['marketId'] for r in pairs]
 if len(pairs)!=100:raise RuntimeError(f'expected100 pairs got{len(pairs)}')
 oos=[];frep=[]
 for fi,(a0,a1,b0,b1) in enumerate(FOLDS,1):
  tr=pairs[a0:a1];te=pairs[b0:b1];xg=np.asarray([r['geom'] for r in tr],float);xgg=np.asarray([r['geom'] for r in te],float);xs=np.asarray([r['geom']+r['snap'] for r in tr],float);xss=np.asarray([r['geom']+r['snap'] for r in te],float);xt=np.asarray([r['geom']+r['snap']+r['traj'] for r in tr],float);xtt=np.asarray([r['geom']+r['snap']+r['traj'] for r in te],float)
  rng=np.random.default_rng(260907+fi);trperm=rng.permutation(len(tr));teperm=rng.permutation(len(te));xsh=np.asarray([r['geom']+r['snap']+tr[trperm[i]]['traj'] for i,r in enumerate(tr)],float);xshh=np.asarray([r['geom']+r['snap']+te[teperm[i]]['traj'] for i,r in enumerate(te)],float)
  pr={t:{} for t in TARGETS};sc={}
  for ti,t in enumerate(TARGETS):
   y=np.asarray([r['targets'][t] for r in tr],float);yy=np.asarray([r['targets'][t] for r in te],float);sc[t]=max(float(np.std(y)),.25);pr[t]['true']=yy;pr[t]['MECHANICAL']=np.asarray([r['mechanical'][t] for r in te],float)
   for kind in ('EXTRATREES','RIDGE'):
    pr[t][kind+'_GEOM']=fit(kind,xg,y,xgg,260000+fi*100+ti);pr[t][kind+'_SNAP']=fit(kind,xs,y,xss,261000+fi*100+ti);pr[t][kind+'_TRAJ']=fit(kind,xt,y,xtt,262000+fi*100+ti);pr[t][kind+'_SHUFFLED_TRAJ']=fit(kind,xsh,y,xshh,263000+fi*100+ti)
  fr={'fold':fi,'trainMarkets':len(tr),'testMarkets':len(te),'mae':{}}
  for t in TARGETS:fr['mae'][t]={k:float(np.mean(np.abs(pr[t]['true']-v))) for k,v in pr[t].items() if k!='true'}
  frep.append(fr)
  for j,r in enumerate(te):
   z={'fold':fi,'marketId':r['marketId'],'vectorError':{}}
   for model in ('MECHANICAL','EXTRATREES_GEOM','EXTRATREES_SNAP','EXTRATREES_TRAJ','EXTRATREES_SHUFFLED_TRAJ','RIDGE_GEOM','RIDGE_SNAP','RIDGE_TRAJ','RIDGE_SHUFFLED_TRAJ'):z['vectorError'][model]=float(np.mean([abs(float(pr[t][model][j])-float(pr[t]['true'][j]))/sc[t] for t in TARGETS]))
   oos.append(z)
 def gate(new,base):
  ne=np.asarray([x['vectorError'][new] for x in oos]);ba=np.asarray([x['vectorError'][base] for x in oos]);d=ba-ne;im=int(np.sum(d>EPS));wo=int(np.sum(d<-EPS));return {'comparison':new+'_vs_'+base,'improvedMarkets':im,'worsenedMarkets':wo,'tiedMarkets':len(d)-im-wo,'improvedMarketRate':im/len(d),'meanVectorErrorImprovement':float(np.mean(d)),'p10PerMarketImprovement':q(d,.1),'aggregateErrorImproves':bool(np.mean(d)>0),'seventyPercentGate':bool(im/len(d)>=.70),'tailGuard':bool(q(ne,.9)<=1.10*q(ba,.9)+EPS)}
 snap=gate('EXTRATREES_SNAP','EXTRATREES_GEOM');traj=gate('EXTRATREES_TRAJ','EXTRATREES_SNAP');shuffle=gate('EXTRATREES_TRAJ','EXTRATREES_SHUFFLED_TRAJ');ridge=gate('RIDGE_TRAJ','RIDGE_SNAP');traj['developmentGatePass']=bool(traj['seventyPercentGate'] and traj['aggregateErrorImproves'] and traj['tailGuard'] and shuffle['aggregateErrorImproves'])
 verdict='RESPONSIBILITY_TRAJECTORY_VALUE_ESTABLISHED' if traj['developmentGatePass'] else ('PARTIAL_RESPONSIBILITY_TRAJECTORY_SIGNAL' if traj['aggregateErrorImproves'] and shuffle['aggregateErrorImproves'] and traj['improvedMarketRate']>=.60 else 'RESPONSIBILITY_TRAJECTORY_NOT_ESTABLISHED')
 out={'version':'MANAGEMENT_PHASEB_RESPONSIBILITY_SERVICE_TRAJECTORY_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'markets':len(pairs),'trajectorySemantics':'event-clock prior OUR decision-state evolution; current native action excluded','lagEvents':LAGS,'windowEvents':WINDOWS,'snapshotFeatures':SNAP,'trajectoryFeatureCount':len(pairs[0]['traj']),'folds':frep,'snapshotGate':snap,'trajectoryGate':traj,'shuffleControl':shuffle,'ridgeRobustness':ridge,'verdict':verdict,'boundary':['strict forward markets 40->20,60->20,80->20','trajectory uses only decision_ms < fork plus current pre-submit state','no current native action label/class in trajectory','event-count lags/windows are research representation, not fixed-time trading rules','no future execution labels in trajectory','shuffle control preregistered in same run','fixed ExtraTrees primary/Ridge secondary; no tuning','70/30 development analogue only','no policy authority/no winner/Target future/no NEW24-B/no dream fill/no 8781']}
 op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'verdict':verdict,'snapshot':snap,'trajectory':traj,'shuffle':shuffle,'ridge':ridge,'trajectoryFeatureCount':len(pairs[0]['traj'])},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
