from __future__ import annotations
import argparse,json,importlib.util
from pathlib import Path
import numpy as np,pandas as pd
SEED=20260907
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
TRAIN=LANE/'C2_FRESH_PROGRAM_SOURCE_FEATURES_V1.csv'
s1=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(s1);s1.loader.exec_module(v1)
s2=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(s2);s2.loader.exec_module(v2)

def predecessor(z,cur):
 q=z[(z.target_side==cur.target_side)&(z.placement_first_ms<cur.placement_first_ms)&z.post_action.isin(['SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT','REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT'])].copy();q=q[q.post_action_native_price.notna()]
 if not len(q):return None
 exact=q[(q.post_action_native_price-cur.native_price).abs()<=1e-6]
 if len(exact):q=exact
 else:
  q=q[(q.post_action_native_price-cur.native_price).abs()<=0.0100001]
  if not len(q):return None
 q=q[(cur.placement_first_ms-q.placement_first_ms)<=5000]
 return q.sort_values('placement_first_ms').iloc[-1] if len(q) else None

def traces_pre(z,cur,entry,max_depth=8):
 node=cur;seen=set()
 for _ in range(max_depth):
  p=predecessor(z,node)
  if p is None:return False
  k=str(p.order_hash)
  if k in seen:return False
  seen.add(k)
  if p.placement_first_ms<entry:return True
  node=p
 return False

def build(datadir):
 datadir=Path(datadir);a=pd.read_csv(datadir/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',low_memory=False);c=pd.read_csv(datadir/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',low_memory=False);p=pd.read_csv(datadir/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv',low_memory=False)
 e=v1.build_entries(a,c);e=v2.add_end(e,a,c);e=e[e.net_aligned_prior & e.full5s_observed].copy()
 p=p[(p.placement_coverage>=.85)&(p.fill_allocation_coverage>=.70)&p.placement_first_ms.notna()&p.first_target_ms.notna()&(p.placement_first_ms<=p.first_target_ms)].copy();by={int(m):z.sort_values('placement_first_ms') for m,z in p.groupby('market_id',sort=False)}
 fresh=[];traced=[];hasparent=[]
 for r in e.itertuples():
  z=by.get(int(r.market_id));q=None if z is None else z[(z.target_side==r.anchor)&(z.placement_first_ms>r.entry_ms)&(z.placement_first_ms<=r.entry_ms+5000)]
  if q is None or len(q)==0:hasparent.append(0);traced.append(0);fresh.append(0);continue
  cur=q.iloc[0];tp=traces_pre(z,cur,int(r.entry_ms));hasparent.append(1);traced.append(int(tp));fresh.append(int(not tp))
 e['parent_admission']=hasparent;e['traced_program_continuation']=traced;e['fresh_program_admission']=fresh
 e['phase']=e.seconds_left/300;e['cur_predict_support']=e.predict_support;e['cur_strike_support']=e.strike_support;e['last_age_log2']=np.log1p(e.last_action_age_ms.clip(lower=0))
 return e

def prepare_fit(train,cols):
 x,_=v1.prepare(train[cols],train[cols]);b=v1.fit_logit(x,train.fresh_program_admission.to_numpy(int));
 # reproduce training transform exactly for external data requires own med/mu/sd; implement explicitly
 raw=np.asarray(train[cols],float);keep=np.isfinite(raw).any(0);raw=raw[:,keep];am=~np.isfinite(raw);med=np.nanmedian(np.where(am,np.nan,raw),axis=0);filled=np.where(am,med,raw);mu=filled.mean(0);sd=filled.std(0);sd[sd<1e-8]=1;masks=am.any(0)
 return {'beta':b,'keep':keep,'med':med,'mu':mu,'sd':sd,'masks':masks}
def predict(model,df,cols):
 raw=np.asarray(df[cols],float)[:,model['keep']];miss=~np.isfinite(raw);filled=np.where(miss,model['med'],raw);x=np.c_[np.ones(len(raw)),np.clip((filled-model['mu'])/model['sd'],-8,8),miss[:,model['masks']]];return 1/(1+np.exp(-np.clip(x@model['beta'],-35,35)))
def cluster_delta(d,p0,p1,resamples=5000):return v2.market_cluster_delta(d,p0,p1,label='fresh_program_admission',resamples=resamples)
def evaluate(train,test,train_name):
 groups={'CURRENT':['phase','cur_predict_support','cur_strike_support'],'PLUS_SERVICE_AGE':['phase','cur_predict_support','cur_strike_support','last_age_log2']};pred={};met={}
 for n,cols in groups.items():
  m=prepare_fit(train,cols);p=predict(m,test,cols);pred[n]=p;met[n]=v1.metrics(test.fresh_program_admission.to_numpy(int),p)
 return {'trainName':train_name,'trainRows':len(train),'trainMarkets':int(train.market_id.nunique()),'metrics':met,'increment':cluster_delta(test,pred['CURRENT'],pred['PLUS_SERVICE_AGE'])}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--datadir',required=True);ap.add_argument('--output',required=True);ap.add_argument('--csv');a=ap.parse_args();d=build(a.datadir);tr=pd.read_csv(TRAIN,low_memory=False); tr['last_age_log2']=np.log1p(tr.last_action_age_ms.clip(lower=0))
 allfit=evaluate(tr,d,'CANONICAL417_ALL');trainfit=evaluate(tr[tr.split.eq('TRAIN')],d,'CANONICAL_TRAIN261') if len(tr) else None
 out={'version':'OUR_C2_FRESH_SERVICE_AGE_INDEPENDENT_REPLICATION_V1','status':'RESEARCH_ONLY','externalCohort':{'rows':len(d),'markets':int(d.market_id.nunique()),'parentAdmissions':int(d.parent_admission.sum()),'tracedPrograms':int(d.traced_program_continuation.sum()),'freshAdmissions':int(d.fresh_program_admission.sum()),'freshRate':float(d.fresh_program_admission.mean()) if len(d) else None},'canonicalAllFit':allfit,'canonicalTrainOnlySensitivity':trainfit,'guards':['External cohort markets were excluded from canonical120 before candidate service-age seam was selected.','Cohort is temporally older than canonical120, so this is independent backward-time replication, not prospective forward holdout.','Fresh admission uses first HQ same-side Maker parent within 5s, then removes chains traceable to pre-conflict same-side reprice/refill program.','Model forms and ridge penalty are frozen from canonical analysis; no external threshold tuning.']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');
 if a.csv:d.to_csv(a.csv,index=False)
 print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
