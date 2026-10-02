from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'data/research/r4_v0/hourly'
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FILES={
 'FRESH24':H/'r4_management_hft_shadow_fresh24_v1_rows.csv',
 'UNSEEN24':H/'r4_management_hft_shadow_unseen24_v1_rows.csv',
 'REPLICATION3':H/'r4_management_hft_shadow_replication3_v1_rows.csv'}
FEATS=['weak_oldest_age_s','weak_age_over_mode','event_decay_15','event_accel_5v15','weak_active_owners']

def prep(path):
 d=pd.read_csv(path)
 d=d[(d.seconds_left<=180)&(d.seconds_left>60)&(d.build_now==1)].copy()
 ps=d[['p_continue_full','p_handoff_full','p_observe_full']].to_numpy(float)
 labs=np.array(['CONTINUE','HANDOFF','OBSERVE'])
 d['decision']=labs[np.argmax(ps,axis=1)]
 d['teacher']=d.management_label_5s.map({'CONTINUE_WEAK':'CONTINUE','HANDOFF_ALLOW':'HANDOFF','OBSERVE_NO_EVENT':'OBSERVE'}).fillna('UNKNOWN')
 mode=np.maximum(d.current_mode_age_s.to_numpy(float),1.0)
 d['weak_age_over_mode']=d.weak_oldest_age_s.to_numpy(float)/mode
 d['event_decay_15']=-(d.events_15s.to_numpy(float))
 d['event_accel_5v15']=d.events_5s.to_numpy(float)-(d.events_15s.to_numpy(float)/3.0)
 return d

def fit(train):
 c=train[(train.decision=='CONTINUE') & (train.teacher.isin(['CONTINUE','OBSERVE']))].copy()
 y=(c.teacher=='OBSERVE').astype(int).to_numpy()
 X=c[FEATS].fillna(0).to_numpy(float)
 sc=StandardScaler().fit(X)
 m=LogisticRegression(C=0.5,class_weight='balanced',solver='liblinear',random_state=4401).fit(sc.transform(X),y)
 return sc,m

def eval_one(name,d,sc,m):
 c=d[(d.decision=='CONTINUE') & (d.teacher.isin(['CONTINUE','OBSERVE']))].copy()
 X=c[FEATS].fillna(0).to_numpy(float)
 p=m.predict_proba(sc.transform(X))[:,1]
 c['p_unnecessary']=p
 c['would_veto']=p>=0.5
 y=(c.teacher=='OBSERVE').astype(int).to_numpy()
 pred=(p>=0.5).astype(int)
 auc=float(roc_auc_score(y,p)) if len(set(y))>1 else None
 ba=float(balanced_accuracy_score(y,pred)) if len(set(y))>1 else None
 err=c.teacher=='OBSERVE'; good=c.teacher=='CONTINUE'
 veto_err=int((c.would_veto & err).sum()); veto_good=int((c.would_veto & good).sum())
 base_err=int(err.sum()); base_good=int(good.sum())
 # Counterfactual local economics: a veto is beneficial if baseline CONTINUE had no desired economic progress over next 5s;
 # harmful if baseline CONTINUE actually improved floor or reduced absNet. Scoring-only labels, never runtime inputs.
 econ_progress=((c.floorImproved5s>0)|(c.absNetReduced5s>0))
 veto_no_progress=int((c.would_veto & ~econ_progress).sum())
 veto_with_progress=int((c.would_veto & econ_progress).sum())
 exec_fill=(c.futureWeakMakerFill5s>0)
 veto_no_fill=int((c.would_veto & ~exec_fill).sum())
 veto_with_fill=int((c.would_veto & exec_fill).sum())
 rows=[]
 for _,r in c.iterrows():
  rows.append({'marketId':int(r.marketId),'t':int(r.t),'seconds_left':float(r.seconds_left),'baselineDecision':'CONTINUE','teacher':str(r.teacher),'pUnnecessary':float(r.p_unnecessary),'shadowDecision':'OBSERVE' if bool(r.would_veto) else 'CONTINUE','futureEconomicProgress5s':bool((r.floorImproved5s>0)|(r.absNetReduced5s>0)),'futureWeakFill5s':bool(r.futureWeakMakerFill5s>0)})
 return {'rows':int(len(c)),'markets':int(c.marketId.nunique()),'errors':base_err,'correctContinues':base_good,'auc':auc,'balancedAccuracy':ba,'wouldVeto':int(c.would_veto.sum()),'errorsRemoved':veto_err,'errorRemovalRate':float(veto_err/max(1,base_err)),'correctContinuesVetoed':veto_good,'correctContinuePreservation':float((base_good-veto_good)/max(1,base_good)),'vetoNoEconomicProgress':veto_no_progress,'vetoWithEconomicProgress':veto_with_progress,'vetoNoWeakFill':veto_no_fill,'vetoWithWeakFill':veto_with_fill,'annotations':rows}

def main():
 ds={k:prep(v) for k,v in FILES.items()}
 sc,m=fit(ds['FRESH24'])
 out={'version':'R4_MANAGEMENT_NECESSITY_INTEGRATED_SHADOW_V1','researchOnly':True,'actionAuthority':False,'strictPastRuntimeFeatures':True,'realisticHFT':True,'dreamFill':False,'sealed20260816ExcludedBySourceCohorts':True,'trainCohort':'FRESH24','fixedModel':{'family':'LogisticRegression','C':0.5,'classWeight':'balanced','threshold':0.5,'features':FEATS},'note':'Consumed cohorts; development/integrated-shadow evidence only. Future labels used only for scoring annotations. No policy action changed.','cohorts':{}}
 for k,d in ds.items(): out['cohorts'][k]=eval_one(k,d,sc,m)
 hold=[out['cohorts']['UNSEEN24'],out['cohorts']['REPLICATION3']]
 out['holdoutSummary']={'meanAuc':float(np.mean([x['auc'] for x in hold])),'meanBA':float(np.mean([x['balancedAccuracy'] for x in hold])),'errorsRemoved':int(sum(x['errorsRemoved'] for x in hold)),'errorsTotal':int(sum(x['errors'] for x in hold)),'correctContinuesVetoed':int(sum(x['correctContinuesVetoed'] for x in hold)),'correctContinuesTotal':int(sum(x['correctContinues'] for x in hold)),'vetoNoEconomicProgress':int(sum(x['vetoNoEconomicProgress'] for x in hold)),'vetoWithEconomicProgress':int(sum(x['vetoWithEconomicProgress'] for x in hold))}
 out['interpretation']='Tests whether objective-relative lifecycle staleness can reduce the dominant CONTINUE->OBSERVE trajectory failure when inserted as a non-authoritative shadow veto. Promotion requires new market-disjoint whole-market realistic-HFT chronology.'
 path=P/'r4_management_necessity_integrated_shadow_v1.json';path.write_text(json.dumps(out,indent=2),encoding='utf-8')
 print(json.dumps({k:v for k,v in out.items() if k!='cohorts'}|{'cohorts':{k:{kk:vv for kk,vv in v.items() if kk!='annotations'} for k,v in out['cohorts'].items()}},indent=2))
if __name__=='__main__':main()
