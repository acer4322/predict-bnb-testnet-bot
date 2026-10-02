from __future__ import annotations
import json,importlib.util
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907');D=LANE/'unseen_older173_v1';SRC=LANE/'UNSEEN_OLDER173_SERVICE_REPLICATION_V1.csv';OUT=LANE/'UNSEEN_OLDER173_PHASE_ACTIVITY_PLACEBO_V1.json'
s=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(s);s.loader.exec_module(v1)
s2=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(s2);s2.loader.exec_module(v2)

def fit_phase(d,label):
 y=d[label].to_numpy(int);rate=np.clip(y.mean(),1e-6,1-1e-6);p0=np.full(len(d),rate);x=np.c_[np.ones(len(d)),(d.phase-d.phase.mean())/(d.phase.std() or 1)];b=v1.fit_logit(x,y);p=1/(1+np.exp(-np.clip(x@b,-35,35)));return {'intercept':v1.metrics(y,p0),'phase':v1.metrics(y,p),'increment':v2.market_cluster_delta(d,p0,p,label=label,resamples=5000)}
def qtab(d,label,edges):
 z=d.copy();z['bin']=pd.cut(z.phase,edges,include_lowest=True,duplicates='drop');return [{'bin':str(k),'n':int(len(g)),'markets':int(g.market_id.nunique()),'rate':float(g[label].mean())} for k,g in z.groupby('bin',observed=True)]
def main():
 d=pd.read_csv(SRC,low_memory=False);d['phase']=d.seconds_left/300;a=pd.read_csv(D/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',low_memory=False);p=pd.read_csv(D/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv',low_memory=False);p=p[(p.placement_coverage>=.85)&(p.fill_allocation_coverage>=.70)&p.placement_first_ms.notna()&p.first_target_ms.notna()&(p.placement_first_ms<=p.first_target_ms)].copy();ba={int(m):z.sort_values('event_ms') for m,z in a.groupby('market_id',sort=False)};bp={int(m):z.sort_values('placement_first_ms') for m,z in p.groupby('market_id',sort=False)}
 anyact=[];anypar=[];samepar=[];opppar=[];firstsame=[]
 for r in d.itertuples():
  aa=ba[int(r.market_id)];qa=aa[(aa.event_ms>r.entry_ms)&(aa.event_ms<=r.entry_ms+5000)];anyact.append(int(len(qa)>0))
  pp=bp.get(int(r.market_id));qp=pp[(pp.placement_first_ms>r.entry_ms)&(pp.placement_first_ms<=r.entry_ms+5000)] if pp is not None else pp
  if qp is None or len(qp)==0:anypar.append(0);samepar.append(0);opppar.append(0);firstsame.append(np.nan)
  else:
   anypar.append(1);samepar.append(int((qp.target_side==r.anchor).any()));opppar.append(int((qp.target_side!=r.anchor).any()));firstsame.append(int(str(qp.iloc[0].target_side)==r.anchor))
 d['any_action_5s']=anyact;d['any_hq_parent_5s']=anypar;d['same_hq_parent_5s']=samepar;d['opposite_hq_parent_5s']=opppar;d['first_parent_same_side']=firstsame
 labels=['fresh_program_admission','any_action_5s','any_hq_parent_5s','same_hq_parent_5s','opposite_hq_parent_5s'];models={x:fit_phase(d,x) for x in labels};edges=np.unique(np.nanquantile(d.phase,[0,.25,.5,.75,1]));tabs={x:qtab(d,x,edges) for x in labels}
 cond=d[d.any_hq_parent_5s.eq(1)&d.first_parent_same_side.notna()].copy();cond['first_parent_same_side']=cond.first_parent_same_side.astype(int);conditional=fit_phase(cond,'first_parent_same_side') if len(cond)>10 else None
 # fresh share among same-side parent admissions: isolates quote-program freshness rather than activity
 c2=d[d.parent_admission.eq(1)].copy();c2['fresh_given_same_parent']=c2.fresh_program_admission.astype(int);fresh_given=fit_phase(c2,'fresh_given_same_parent') if len(c2)>10 else None
 out={'version':'OUR_C2_PHASE_ACTIVITY_PLACEBO_V1','status':'RESEARCH_ONLY','rows':len(d),'markets':int(d.market_id.nunique()),'phaseModels':models,'phaseQuartiles':tabs,'conditionalFirstParentSameSide':conditional,'freshGivenSameSideParent':fresh_given,'counts':{x:int(d[x].sum()) for x in labels},'guards':['External older173 cohort only; no canonical tuning in this placebo test.','Generic activity labels test whether phase effect is merely higher action/parent hazard.','first_parent_same_side conditions on any HQ parent placement; descriptive because opposite-side parent often serves Repair liability.','No fixed seconds threshold or runtime gate is inferred.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
