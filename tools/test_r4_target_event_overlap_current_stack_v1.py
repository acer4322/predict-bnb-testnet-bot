from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, precision_recall_fscore_support, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_target_event_overlap_current_stack_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_target_event_overlap_current_stack_v1_rows.csv'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
CTX=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
PREP=CTX+['placement_readiness_native_5s']
ATTR=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_edge','strike_toward_dominant_bps','spot_supports_dominant','predict_supports_dominant','placement_readiness_native_5s','price']
def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=260,random_state=seed)
def auc(y,p): return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def ap(y,p): return float(average_precision_score(y,p)) if np.sum(y)>0 else None
def cls(y,p,thr=.5):
 z=(np.asarray(p)>=thr).astype(int); y=np.asarray(y).astype(int)
 pr,rc,f1,_=precision_recall_fscore_support(y,z,average='binary',zero_division=0)
 return {'threshold':float(thr),'predRate':float(z.mean()),'trueRate':float(y.mean()),'accuracy':float((z==y).mean()),'balancedAccuracy':float(balanced_accuracy_score(y,z)),'precision':float(pr),'recall':float(rc),'f1':float(f1),'tp':int(((z==1)&(y==1)).sum()),'fp':int(((z==1)&(y==0)).sum()),'fn':int(((z==0)&(y==1)).sum()),'tn':int(((z==0)&(y==0)).sum())}
def aggregate_events(te):
 def agg(g):
  w=np.maximum(g.shares.fillna(0).to_numpy(float),1e-9); ws=w/w.sum()
  return pd.Series({'seconds_left':float(np.sum(g.seconds_left*ws)),'target_build':int(np.sum(g.build*ws)>=.5),'target_prepare':int(g.prepare_weak5.max()),'p_build':float(np.sum(g.p_build*ws)),'p_prepare_ctx':float(np.sum(g.p_prepare_ctx*ws)),'p_prepare_current':float(np.sum(g.p_prepare_current*ws)),'price':float(np.sum(g.price*ws)),'pre_abs_payoff_gap':float(np.sum(g.pre_abs_payoff_gap*ws)),'pre_risk_deficit':float(np.sum(g.pre_risk_deficit*ws)),'predict_edge':float(np.sum(g.predict_edge*ws)),'strike_toward_dominant_bps':float(np.sum(g.strike_toward_dominant_bps*ws)),'spot_supports_dominant':float(np.sum(g.spot_supports_dominant*ws)),'predict_supports_dominant':float(np.sum(g.predict_supports_dominant*ws)),'placement_readiness_native_5s':float(np.sum(g.placement_readiness_native_5s*ws))})
 return te.groupby(['market_id','first_event_ms'],sort=True).apply(agg,include_groups=False).reset_index()
def transitions(ev,prob_col,truth=False):
 out=[]
 for mid,g in ev.sort_values(['market_id','first_event_ms']).groupby('market_id'):
  prev=None
  for r in g.itertuples():
   s=int(getattr(r,'target_build') if truth else getattr(r,prob_col)>=.5)
   if prev is not None and s!=prev: out.append({'market_id':int(mid),'ms':int(r.first_event_ms),'direction':'ALLOW_TO_BUILD' if s==1 else 'BUILD_TO_ALLOW','seconds_left':float(r.seconds_left)})
   prev=s
 return out
def match_trans(tgt,pred,tol):
 by={}
 for p in pred: by.setdefault((p['market_id'],p['direction']),[]).append(p)
 used=set();lags=[];matched=[]
 for t in tgt:
  cand=[]
  for p in by.get((t['market_id'],t['direction']),[]):
   key=(p['market_id'],p['ms'],p['direction'])
   if key in used: continue
   d=abs(p['ms']-t['ms'])
   if d<=tol: cand.append((d,p))
  if cand:
   _,p=min(cand,key=lambda x:x[0]); key=(p['market_id'],p['ms'],p['direction']);used.add(key);lags.append(p['ms']-t['ms']);matched.append((t,p))
 rec=len(matched)/len(tgt) if tgt else 0.;prec=len(matched)/len(pred) if pred else 0.;f=2*rec*prec/(rec+prec) if rec+prec else 0.
 return {'targetEvents':len(tgt),'predEvents':len(pred),'matched':len(matched),'recall':rec,'precision':prec,'f1':f,'medianLagMs':float(np.median(lags)) if lags else None,'medianAbsLagMs':float(np.median(np.abs(lags))) if lags else None}
def phase(x): return 'LATE_0_60' if x<60 else ('MID_60_180' if x<180 else 'EARLY_180_300')
def mismatch_desc(df,target,pred):
 y=df[target].to_numpy(int); z=(df[pred].to_numpy(float)>=.5).astype(int); tags=np.where((y==1)&(z==0),'MISS_BUILD',np.where((y==0)&(z==1),'FALSE_BUILD',np.where(y==1,'HIT_BUILD','HIT_ALLOW')))
 q=df.copy();q['mismatch']=tags;q['phase']=q.seconds_left.map(phase);o={}
 for tag,g in q.groupby('mismatch'):
  o[tag]={'n':int(len(g)),'phase':{str(k):int(v) for k,v in g.phase.value_counts().items()},'medians':{c:float(g[c].median()) for c in ATTR if c in g and g[c].notna().any()}}
 return o
def main():
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p.market_id=p.market_id.astype(int);p['predict_edge']=(p.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p=p.dropna(subset=NATIVE+[lab,'decision_sampled_at_ms']).copy();p[lab]=p[lab].astype(int)
 src=hgb(20265001);src.fit(p[NATIVE],p[lab])
 f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f=f.dropna(subset=CTX+NATIVE+['first_event_ms','side','weak_side','is_add','shares','price']).copy();f['placement_readiness_native_5s']=src.predict_proba(f[NATIVE])[:,1]
 # Re-drop after generated readiness, then Target labels exactly as prior parallel-head research.
 f=f.dropna(subset=PREP).sort_values(['market_id','first_event_ms']).copy();g=f.groupby('market_id');f['next_ms']=g.first_event_ms.shift(-1);f['next_side']=g.side.shift(-1);f=f[f.next_ms.notna()].copy();f['dt']=f.next_ms-f.first_event_ms;f['prepare_weak5']=((f.dt>0)&(f.dt<=5000)&(f.next_side==f.weak_side)).astype(int);f['build']=(f.is_add.astype(int)==0).astype(int)
 ms=f.groupby('market_id',as_index=False).first_event_ms.min().sort_values('first_event_ms').market_id.astype(int).tolist();initial=55;rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4): sizes[i]+=1
 cur=initial;blocks=[];alltest=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=f[f.market_id.isin(set(trm))].copy();te=f[f.market_id.isin(set(tem))].copy()
  mb=hgb(7100+bi);mc=hgb(7200+bi);mr=hgb(7300+bi);mb.fit(tr[CTX],tr.build);mc.fit(tr[CTX],tr.prepare_weak5);mr.fit(tr[PREP],tr.prepare_weak5)
  te['p_build']=mb.predict_proba(te[CTX])[:,1];te['p_prepare_ctx']=mc.predict_proba(te[CTX])[:,1];te['p_prepare_role']=mr.predict_proba(te[PREP])[:,1];te['p_prepare_current']=np.where(te.seconds_left<60,te.p_prepare_ctx,te.p_prepare_role)
  tr_ctx=mc.predict_proba(tr[CTX])[:,1];tr_role=mr.predict_proba(tr[PREP])[:,1];tr_cur=np.where(tr.seconds_left.to_numpy()<60,tr_ctx,tr_role);rate=float(tr.prepare_weak5.mean());thr=float(np.quantile(tr_cur,max(0,min(1,1-rate))))
  ev=aggregate_events(te); tgt=transitions(ev,'p_build',truth=True);pred=transitions(ev,'p_build',truth=False)
  transres={str(t):match_trans(tgt,pred,t) for t in (1000,2000,5000)}
  b={'block':bi,'testMarkets':tem,'rows':int(len(te)),'eventTimestamps':int(len(ev)),'buildAuc':auc(te.build,te.p_build),'buildAP':ap(te.build,te.p_build),'buildClassification':cls(te.build,te.p_build,.5),'prepareCtxAuc':auc(te.prepare_weak5,te.p_prepare_ctx),'prepareCurrentAuc':auc(te.prepare_weak5,te.p_prepare_current),'prepareCtxNatural':cls(te.prepare_weak5,te.p_prepare_ctx,.5),'prepareCurrentNatural':cls(te.prepare_weak5,te.p_prepare_current,.5),'prepareCurrentRateMatched':cls(te.prepare_weak5,te.p_prepare_current,thr),'rateMatchedThreshold':thr,'transitionOverlap':transres}
  blocks.append(b);te['block']=bi;alltest.append(te)
 d=pd.concat(alltest,ignore_index=True);d.to_csv(ROWS,index=False);ev=aggregate_events(d)
 tgt=transitions(ev,'p_build',truth=True);pred=transitions(ev,'p_build',truth=False)
 # direction/phase transition decomposition at 5s
 decomp={}
 for direc in ('ALLOW_TO_BUILD','BUILD_TO_ALLOW'):
  tt=[x for x in tgt if x['direction']==direc];pp=[x for x in pred if x['direction']==direc];decomp[direc]=match_trans(tt,pp,5000)
 for ph in ('EARLY_180_300','MID_60_180','LATE_0_60'):
  tt=[x for x in tgt if phase(x['seconds_left'])==ph];pp=[x for x in pred if phase(x['seconds_left'])==ph];decomp[ph]=match_trans(tt,pp,5000)
 # summarize false/miss event context at row-level; price is retrospective diagnostic only.
 mismatch=mismatch_desc(d,'build','p_build')
 summary={'rows':int(len(d)),'markets':int(d.market_id.nunique()),'eventTimestamps':int(len(ev)),'build':{'auc':auc(d.build,d.p_build),'ap':ap(d.build,d.p_build),'natural':cls(d.build,d.p_build,.5)},'prepare':{'ctxAuc':auc(d.prepare_weak5,d.p_prepare_ctx),'currentPhaseRoutedAuc':auc(d.prepare_weak5,d.p_prepare_current),'ctxAP':ap(d.prepare_weak5,d.p_prepare_ctx),'currentAP':ap(d.prepare_weak5,d.p_prepare_current)},'transitionOverlap':{str(t):match_trans(tgt,pred,t) for t in (1000,2000,5000)},'transitionDecomposition5s':decomp,'mismatchAttribution':mismatch}
 art={'version':'R4_TARGET_EVENT_OVERLAP_CURRENT_STACK_V1','researchOnly':True,'actionAuthority':False,'question':'How closely do the current portable R4 Formation beliefs overlap Target Maker Formation events in mode and transition timing, and where are residual mismatches concentrated?','currentStack':['portable BUILD head: portfolio geometry + Predict/strike','PREPARE head: placement readiness role-routed in 60-300s, context-only in 0-60s'],'coverage':summary,'blocks':blocks,'guards':['Expanding chronological Target blocks; no winner/settlement.','0.5 is reported only as the natural mode boundary; no threshold sweep.','PREPARE additionally reports a train-prevalence rate-matched diagnostic threshold, never action authority.','Target action price is used only in retrospective mismatch description, never in R4 prediction.','This is event-overlap diagnosis, not strategy promotion.'],'rowsArtifact':str(ROWS.relative_to(ROOT)).replace('\\','/')};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'blocks':[{'block':b['block'],'buildAuc':b['buildAuc'],'buildF1':b['buildClassification']['f1'],'prepCtxAuc':b['prepareCtxAuc'],'prepCurrentAuc':b['prepareCurrentAuc'],'transition5s':b['transitionOverlap']['5000']} for b in blocks]},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
