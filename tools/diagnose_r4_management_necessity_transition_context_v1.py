from pathlib import Path
import json, numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
ROOT=Path.cwd(); H=ROOT/'data/research/r4_v0/hourly'; OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_testbed_v01_necessity_transition_context_v1.json'
coh={'FRESH24':'r4_management_hft_shadow_fresh24_v1_rows.csv','UNSEEN24':'r4_management_hft_shadow_unseen24_v1_rows.csv','REPLICATION3':'r4_management_hft_shadow_replication3_v1_rows.csv'}
# All are strict-past fields already present in frozen shadow rows. Derived ratios are current/past only.
def add(d):
 eps=1e-6
 d=d.copy()
 d['owner_total']=d.weak_active_owners+d.dominant_active_owners
 d['weak_owner_present']=(d.weak_active_owners>0).astype(float)
 d['both_owner_present']=((d.weak_active_owners>0)&(d.dominant_active_owners>0)).astype(float)
 d['owner_age_gap']=d.weak_oldest_age_s-d.dominant_oldest_age_s
 d['weak_age_over_mode']=d.weak_oldest_age_s/(d.current_mode_age_s+eps)
 d['event_accel_5v15']=d.events_5s/(d.events_15s+eps)
 d['transition_density']=d.transitions_15s/(d.events_15s+eps)
 d['quiet_5s']=(d.events_5s<=0).astype(float)
 d['recent_transition']=(d.transitions_15s>0).astype(float)
 d['mode_age_log']=np.log1p(d.current_mode_age_s.clip(lower=0))
 return d
features=['weak_active_owners','weak_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s','owner_total','weak_owner_present','both_owner_present','owner_age_gap','weak_age_over_mode','event_accel_5v15','transition_density','quiet_5s','recent_transition','mode_age_log']
res={}
for name,fn in coh.items():
 d=add(pd.read_csv(H/fn)); d=d[(d.seconds_left<=180)&(d.seconds_left>60)&(d.build_now==1)].copy()
 ps=d[['p_continue_full','p_handoff_full','p_observe_full']].to_numpy(float); labs=np.array(['CONTINUE','HANDOFF','OBSERVE']); d['decision']=labs[np.argmax(ps,axis=1)]
 d['teacher']=d.management_label_5s.map({'CONTINUE_WEAK':'CONTINUE','HANDOFF_ALLOW':'HANDOFF','OBSERVE_NO_EVENT':'OBSERVE'}).fillna('UNKNOWN')
 x=d[(d.decision=='CONTINUE') & d.teacher.isin(['OBSERVE','CONTINUE'])].copy(); x['y']=(x.teacher=='OBSERVE').astype(int)
 fs={}
 for f in features:
  z=x[[f,'y']].replace([np.inf,-np.inf],np.nan).dropna(); auc=float(roc_auc_score(z.y,z[f])) if z.y.nunique()==2 else None
  fs[f]={'auc_error_high':auc,'direction':'HIGHER_ERROR' if auc is not None and auc>0.5 else 'LOWER_ERROR' if auc is not None and auc<0.5 else 'NEUTRAL','errorMedian':float(x.loc[x.y==1,f].median()),'correctMedian':float(x.loc[x.y==0,f].median())}
 res[name]={'n':int(len(x)),'errors':int(x.y.sum()),'features':fs}
stable=[]
for f in features:
 a={c:res[c]['features'][f]['auc_error_high'] for c in coh}; vals=list(a.values()); same=all(v>0.5 for v in vals) or all(v<0.5 for v in vals)
 stable.append({'feature':f,'aucs':a,'sameDirection':same,'meanDistanceFromChance':float(np.mean([abs(v-.5) for v in vals])),'minDistanceFromChance':float(min(abs(v-.5) for v in vals))})
stable.sort(key=lambda z:(z['sameDirection'],z['minDistanceFromChance'],z['meanDistanceFromChance']),reverse=True)
rep={'version':'R4_MANAGEMENT_TESTBED_V0_1_NECESSITY_TRANSITION_CONTEXT_V1','researchOnly':True,'strictPast':True,'noRetuning':True,'cohortsConsumedForRepresentationSelection':True,'question':'Does objective-relative ownership/transition context retain a stable direction for CONTINUE->OBSERVE error across cohorts after static geometry failed?','cohorts':res,'stableRanking':stable,'note':'No future outcome is used as a feature. management_label_5s is teacher/evaluation only.'}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)),'topStable':stable[:8]},indent=2))
