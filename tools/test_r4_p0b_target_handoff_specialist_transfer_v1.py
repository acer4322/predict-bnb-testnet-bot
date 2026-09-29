from __future__ import annotations
import json,lzma,joblib,sys
from pathlib import Path
from collections import defaultdict
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
from interpret.glassbox import ExplainableBoostingClassifier
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_handoff_specialist_transfer_preregistered_v1.json'
HFTPRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_handoff_newroot_replication_late20b_preregistered_v1.json'
SRC_T=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
SRC_H=ROOT/'data/hft_forward_paper_v1/markets'
STACK=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_handoff_specialist_transfer_v1.json'
MODEL_OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_handoff_specialist_v1.joblib'
SEED=260827971

def met(y,p):
 y=np.asarray(y,dtype=int);p=np.asarray(p,dtype=float);o={'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
 if len(y) and len(np.unique(y))>1:o.update({'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))})
 else:o.update({'auc':None,'ap':None,'logLoss':None})
 return o

def core(x):return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}

def active_roots_at(events,cutoff):
 active={};root_of={}
 for idx,e in sorted(enumerate(events),key=lambda z:(int(z[1].get('received_at_ms') or 0),z[0])):
  if int(e.get('received_at_ms') or 0)>cutoff:break
  et=str(e.get('event_type') or '');iid=e.get('intent_id');rid=str(e.get('responsibility_id'))
  if iid:root_of[str(iid)]=rid
  if et=='ACK_NEW' and iid:active[str(iid)]=True
  elif et in {'FULL_FILL','ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL'} and iid:active[str(iid)]=False
  elif et in {'RESPONSIBILITY_COMPLETED','RESPONSIBILITY_TERMINATED'}:
   for x,r in list(root_of.items()):
    if r==rid:active[x]=False
 return {root_of[i] for i,v in active.items() if v and i in root_of}

def main():
 pre=json.loads(PRE.read_text(encoding='utf-8')); hpre=json.loads(HFTPRE.read_text(encoding='utf-8'));F=pre['features']
 d=pd.read_csv(SRC_T).replace([np.inf,-np.inf],np.nan)
 d=d[(d.seconds_left>=60)&(d.seconds_left<180)&(d.build_now==1)&d.management_label_5s.isin(['HANDOFF_ALLOW','OBSERVE_NO_EVENT'])].dropna(subset=F).copy().sort_values(['market_id','t'])
 ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();cut=max(1,int(len(ms)*.8));trm=ms[:cut];tem=ms[cut:]
 tr=d[d.market_id.isin(trm)].copy();te=d[d.market_id.isin(tem)].copy();tr['y']=(tr.management_label_5s=='HANDOFF_ALLOW').astype(int);te['y']=(te.management_label_5s=='HANDOFF_ALLOW').astype(int)
 model=ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=12,n_jobs=-2,random_state=SEED)
 model.fit(tr[F],tr.y);pt=model.predict_proba(te[F])[:,list(model.classes_).index(1)];tmet=met(te.y,pt)
 # HFT transfer episodes, identical teacher semantics to late20b preregistered test.
 old=joblib.load(STACK);m1=old['M1_model'];classes=list(old['classes']);hi=classes.index('HANDOFF_ALLOW');oi=classes.index('OBSERVE_NO_EVENT');full=list(old['features']['full'])
 episodes=[];exact=0
 for mid in map(int,hpre['markets']):
  dd=json.load(lzma.open(SRC_H/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));a=base.simulate(dd,hpre['policy'],collect_shadow=False);b=sim.simulate(dd,hpre['policy'],collect_shadow=True,collect_provenance=True);exact+=int(core(a)==core(b));lr=b.get('managementLifecycleRows') or [];evs=b.get('provenanceJournal') or [];by=defaultdict(list)
  for r in lr:by[str(r['checkpointResponsibilityId'])].append(r)
  for rid,rr in by.items():
   rr=sorted(rr,key=lambda r:int(r['t']));cand=next((r for r in rr if int(r.get('rootResponsibilityPersists5s') or 0)==0),None)
   if cand is None:continue
   t=int(cand['t']);cutoff=t+5000;new_other={str(e.get('responsibility_id')) for e in evs if str(e.get('event_type'))=='ACK_NEW' and t<int(e.get('received_at_ms') or 0)<=cutoff and str(e.get('responsibility_id'))!=rid};y=int(bool(new_other))
   xs=np.asarray([[float(cand[f]) for f in F]],dtype=float);ps=float(model.predict_proba(xs)[0,list(model.classes_).index(1)])
   xo=np.asarray([[float(cand[f]) for f in full]],dtype=float);po=m1.predict_proba(xo)[0];den=float(po[hi]+po[oi]);pb=float(po[hi]/den) if den>1e-12 else .5
   episodes.append({'marketId':mid,'t':t,'responsibilityId':rid,'label':y,'specialistScore':ps,'frozenM1ConditionalScore':pb,'secondsLeft':float(cand['seconds_left']),'absGap':float(cand['abs_gap']),'coverage':float(cand['coverage'])})
 y=[x['label'] for x in episodes];sp=[x['specialistScore'] for x in episodes];bp=[x['frozenM1ConditionalScore'] for x in episodes];hm=met(y,sp);bm=met(y,bp);rule=pre['fixedKeepRule'];checks={'targetHoldoutAuc':tmet.get('auc') is not None and tmet['auc']>=rule['targetHoldoutAucMinimum'],'hftAuc':hm.get('auc') is not None and hm['auc']>=rule['hftAucMinimum'],'hftAucImprovement':hm.get('auc') is not None and bm.get('auc') is not None and hm['auc']-bm['auc']>=rule['hftAucImprovementVsFrozenM1Minimum'],'hftApAboveRate':hm.get('ap') is not None and hm['ap']>hm['rate'],'executionNoRegression':exact==len(hpre['markets'])};status='SPECIALIST_TRANSFER_KEEP' if all(checks.values()) else 'SPECIALIST_TRANSFER_REJECT'
 joblib.dump({'version':'R4_P0B_TARGET_HANDOFF_SPECIALIST_V1','researchOnly':True,'actionAuthority':False,'features':F,'model':model,'targetTrainMarkets':trm,'targetHoldoutMarkets':tem,'status':status},MODEL_OUT)
 rep={'version':'R4_P0B_TARGET_HANDOFF_SPECIALIST_TRANSFER_V1','status':status,'preRegistered':str(PRE.relative_to(ROOT)).replace('\\','/'),'target':{'eligibleRows':int(len(d)),'markets':len(ms),'trainMarkets':len(trm),'holdoutMarkets':len(tem),'trainRows':int(len(tr)),'holdoutRows':int(len(te)),'holdoutMetrics':tmet},'hft':{'markets':len(hpre['markets']),'episodes':len(episodes),'executionCoreExact':f'{exact}/{len(hpre["markets"])}','specialist':hm,'frozenM1ConditionalBaseline':bm,'aucDeltaSpecialistMinusBaseline':(hm['auc']-bm['auc']) if hm.get('auc') is not None and bm.get('auc') is not None else None},'checks':checks,'modelArtifact':str(MODEL_OUT.relative_to(ROOT)).replace('\\','/'),'episodes':episodes,'guards':pre['guards']}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'target':rep['target'],'hft':rep['hft'],'checks':checks,'modelArtifact':rep['modelArtifact']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
