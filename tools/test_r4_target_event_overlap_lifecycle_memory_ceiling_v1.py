from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_target_event_overlap_current_stack_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_target_event_overlap_lifecycle_memory_ceiling_v1.json'
BASE=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
MEM=['prev_target_build','prev_event_dt_ms','prev_mode_age_ms','prev_mode_run_events','recent_events_5s','recent_events_15s','recent_events_30s','recent_transitions_30s']
def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=260,random_state=seed)
def auc(y,p): return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def ap(y,p): return float(average_precision_score(y,p)) if np.sum(y)>0 else None
def build_memory(d):
 out=[]
 for mid,g in d.sort_values(['market_id','first_event_ms']).groupby('market_id'):
  # aggregate one state per timestamp first, preserving share-weighted current features and majority Target mode
  ev=[]
  for t,x in g.groupby('first_event_ms',sort=True):
   w=np.maximum(x.shares.fillna(0).to_numpy(float),1e-9);w=w/w.sum()
   r={'market_id':int(mid),'first_event_ms':int(t),'build':int(np.sum(x.build.to_numpy(float)*w)>=.5)}
   for c in BASE:r[c]=float(np.sum(x[c].to_numpy(float)*w))
   ev.append(r)
  prev_t=None;prev_mode=None;run_start=None;run_events=0;trans=[];times=[]
  for r in ev:
   t=r['first_event_ms'];m=r['build']
   r['prev_target_build']=float(prev_mode) if prev_mode is not None else np.nan
   r['prev_event_dt_ms']=float(t-prev_t) if prev_t is not None else np.nan
   r['prev_mode_age_ms']=float(t-run_start) if run_start is not None else np.nan
   r['prev_mode_run_events']=float(run_events) if prev_mode is not None else np.nan
   for win,name in ((5000,'recent_events_5s'),(15000,'recent_events_15s'),(30000,'recent_events_30s')):
    r[name]=float(sum(1 for tt in times if t-win<=tt<t))
   r['recent_transitions_30s']=float(sum(1 for tt in trans if t-30000<=tt<t))
   out.append(r)
   if prev_mode is None:
    run_start=t;run_events=1
   elif m==prev_mode:
    run_events+=1
   else:
    trans.append(t);run_start=t;run_events=1
   times.append(t);prev_t=t;prev_mode=m
 return pd.DataFrame(out)
def transitions(ev,pcol,truth=False):
 out=[]
 for mid,g in ev.sort_values(['market_id','first_event_ms']).groupby('market_id'):
  prev=None
  for r in g.itertuples():
   s=int(r.build if truth else getattr(r,pcol)>=.5)
   if prev is not None and s!=prev:out.append((int(mid),int(r.first_event_ms),'ALLOW_TO_BUILD' if s else 'BUILD_TO_ALLOW'))
   prev=s
 return out
def match(tgt,pred,tol=5000):
 by={}
 for x in pred:by.setdefault((x[0],x[2]),[]).append(x)
 used=set();lags=[];n=0
 for t in tgt:
  c=[]
  for p in by.get((t[0],t[2]),[]):
   k=p
   if k in used:continue
   dd=abs(p[1]-t[1])
   if dd<=tol:c.append((dd,p))
  if c:
   _,p=min(c,key=lambda x:x[0]);used.add(p);n+=1;lags.append(p[1]-t[1])
 rec=n/len(tgt) if tgt else 0;prec=n/len(pred) if pred else 0;f=2*rec*prec/(rec+prec) if rec+prec else 0
 return {'targetEvents':len(tgt),'predEvents':len(pred),'matched':n,'recall':rec,'precision':prec,'f1':f,'medianAbsLagMs':float(np.median(np.abs(lags))) if lags else None}
def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);ev=build_memory(d);ev=ev.dropna(subset=BASE+MEM).copy()
 ms=ev.groupby('market_id').first_event_ms.min().sort_values().index.astype(int).tolist();initial=max(20,len(ms)-40);rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[];allte=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=ev[ev.market_id.isin(trm)];te=ev[ev.market_id.isin(tem)].copy()
  b=hgb(8100+bi).fit(tr[BASE],tr.build);m=hgb(8200+bi).fit(tr[BASE+MEM],tr.build);te['p_base']=b.predict_proba(te[BASE])[:,1];te['p_mem']=m.predict_proba(te[BASE+MEM])[:,1]
  tgt=transitions(te,'p_base',True);pb=transitions(te,'p_base',False);pm=transitions(te,'p_mem',False)
  blocks.append({'block':bi,'testMarkets':tem,'rows':int(len(te)),'base':{'auc':auc(te.build,te.p_base),'ap':ap(te.build,te.p_base),'overlap5s':match(tgt,pb)},'memory':{'auc':auc(te.build,te.p_mem),'ap':ap(te.build,te.p_mem),'overlap5s':match(tgt,pm)}});allte.append(te)
 te=pd.concat(allte,ignore_index=True);tgt=transitions(te,'p_base',True);pb=transitions(te,'p_base',False);pm=transitions(te,'p_mem',False)
 summary={'markets':int(te.market_id.nunique()),'rows':int(len(te)),'base':{'auc':auc(te.build,te.p_base),'ap':ap(te.build,te.p_base),'overlap5s':match(tgt,pb)},'memory':{'auc':auc(te.build,te.p_mem),'ap':ap(te.build,te.p_mem),'overlap5s':match(tgt,pm)}}
 summary['delta']={'auc':summary['memory']['auc']-summary['base']['auc'],'ap':summary['memory']['ap']-summary['base']['ap'],'transitionRecall':summary['memory']['overlap5s']['recall']-summary['base']['overlap5s']['recall'],'transitionPrecision':summary['memory']['overlap5s']['precision']-summary['base']['overlap5s']['precision'],'transitionF1':summary['memory']['overlap5s']['f1']-summary['base']['overlap5s']['f1'],'predTransitionCount':summary['memory']['overlap5s']['predEvents']-summary['base']['overlap5s']['predEvents']}
 art={'version':'R4_TARGET_EVENT_OVERLAP_LIFECYCLE_MEMORY_CEILING_V1','researchOnly':True,'actionAuthority':False,'purpose':'Diagnose whether strict-past controller lifecycle memory can explain the large Target transition-recall gap left by the current pointwise portable BUILD head.','features':{'base':BASE,'teacherLifecycleMemory':MEM},'summary':summary,'blocks':blocks,'guards':['Teacher previous mode/history is a diagnostic ceiling, not a deployable Target runtime input.','Runtime analogue would have to use OUR own prior mode/order/fill history.','No threshold sweep; 0.5 natural boundary.','No winner/settlement/future state.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
