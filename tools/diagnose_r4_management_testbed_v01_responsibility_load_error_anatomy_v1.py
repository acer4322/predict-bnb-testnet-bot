from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_unseen24_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_testbed_v01_responsibility_load_error_anatomy_v1.json'
d=pd.read_csv(SRC)
d=d[(d.seconds_left<=180)&(d.seconds_left>60)&(d.build_now==1)].copy()
# Existing integrated policy decision from frozen probability heads.
ps=d[['p_continue_full','p_handoff_full','p_observe_full']].to_numpy(float)
labels=np.array(['CONTINUE','HANDOFF','OBSERVE'])
d['decision']=labels[np.argmax(ps,axis=1)]
d['teacher']=d.management_label_5s.map({'CONTINUE_WEAK':'CONTINUE','HANDOFF_ALLOW':'HANDOFF','OBSERVE_NO_EVENT':'OBSERVE'}).fillna('UNKNOWN')
# Frozen representation selected in prior cycle. No threshold sweep here.
gap=np.maximum(d.abs_gap.to_numpy(float),1e-6)
d['qty_gap_ratio']=d.requested_qty.to_numpy(float)/gap
d['weak_owner_gap_ratio']=d.weak_active_owners.to_numpy(float)/gap
# Direction fixed from prior cross-cohort finding: lower qty/gap => more likely completion.
d['load_completion_score']=-d.qty_gap_ratio - 0.25*d.weak_owner_gap_ratio
# Focus on the largest whole-stack error: CONTINUE when teacher says OBSERVE.
d['continue_vs_observe_error']=((d.decision=='CONTINUE')&(d.teacher=='OBSERVE')).astype(int)
d['continue_correct']=((d.decision=='CONTINUE')&(d.teacher=='CONTINUE')).astype(int)
cont=d[d.decision=='CONTINUE'].copy()
def summary(x):
 return {'n':int(len(x)),'markets':int(x.marketId.nunique()),'qtyGapMedian':float(x.qty_gap_ratio.median()) if len(x) else None,'qtyGapMean':float(x.qty_gap_ratio.mean()) if len(x) else None,'ownerGapMedian':float(x.weak_owner_gap_ratio.median()) if len(x) else None,'scoreMedian':float(x.load_completion_score.median()) if len(x) else None,'actualFill5sRate':float((x.futureWeakMakerFill5s>0).mean()) if len(x) else None,'econProgressRate':float(((x.floorImproved5s>0)|(x.absNetReduced5s>0)).mean()) if len(x) else None}
err=cont[cont.teacher=='OBSERVE']; good=cont[cont.teacher=='CONTINUE']; other=cont[~cont.teacher.isin(['OBSERVE','CONTINUE'])]
# AUC orientation: high overload ratio should identify CONTINUE->OBSERVE error.
auc=None
if len(err)>0 and len(good)>0:
 x=pd.concat([err.assign(y=1),good.assign(y=0)],ignore_index=True)
 auc=float(roc_auc_score(x.y,x.qty_gap_ratio))
# Market-paired directional check where both error and correct CONTINUE exist.
paired=[]
for m,g in cont.groupby('marketId'):
 a=g[g.teacher=='OBSERVE']; b=g[g.teacher=='CONTINUE']
 if len(a) and len(b):
  paired.append({'marketId':int(m),'errorQtyGapMedian':float(a.qty_gap_ratio.median()),'correctQtyGapMedian':float(b.qty_gap_ratio.median()),'errorHigher':bool(a.qty_gap_ratio.median()>b.qty_gap_ratio.median())})
rep={'version':'R4_MANAGEMENT_TESTBED_V0_1_RESPONSIBILITY_LOAD_ERROR_ANATOMY_V1','researchOnly':True,'strictPastRuntimeFeatures':True,'developmentDiagnosticOnly':True,'rows':int(len(d)),'markets':int(d.marketId.nunique()),'decisionCounts':{str(k):int(v) for k,v in d.decision.value_counts().items()},'teacherCounts':{str(k):int(v) for k,v in d.teacher.value_counts().items()},'continueRows':int(len(cont)),'continueToObserveError':summary(err),'continueCorrect':summary(good),'continueOther':summary(other),'overloadAuc_ErrorVsCorrectContinue':auc,'pairedMarketCheck':{'markets':len(paired),'errorHigherCount':sum(int(x['errorHigher']) for x in paired),'rows':paired},'interpretation':'No threshold or model tuning. Tests whether the previously selected regime-normalized responsibility-load representation structurally explains the largest integrated Management Testbed error.'}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
