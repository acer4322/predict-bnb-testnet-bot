from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_placement_readiness_role_audit_v1.json'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']

def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.055,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=240,random_state=seed)
def metric(y,p):
 return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p.market_id=p.market_id.astype(int);p['predict_edge']=(p.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p=p.dropna(subset=NATIVE+[lab,'decision_sampled_at_ms']).copy();p[lab]=p[lab].astype(int)
 src=hgb(20263401);src.fit(p[NATIVE],p[lab])
 f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f=f.dropna(subset=NATIVE+['first_event_ms','is_add']).copy();f['repair']=1-f.is_add.astype(int);f['readiness']=src.predict_proba(f[NATIVE])[:,1]
 # Later-cohort semantic label: after this Target Maker parent onset, is another Target Maker parent onset observed within 5s in the same market?
 f=f.sort_values(['market_id','first_event_ms']).copy();f['next_parent_ms']=f.groupby('market_id').first_event_ms.shift(-1);f['next_parent_dt_ms']=f.next_parent_ms-f.first_event_ms;f['next_parent_5s']=((f.next_parent_dt_ms>0)&(f.next_parent_dt_ms<=5000)).astype(int)
 # Exclude last row in each market because absence may just be end-of-window / no next observation.
 z=f[f.next_parent_ms.notna()].copy()
 ms=z.groupby('market_id',as_index=False).first_event_ms.min().sort_values('first_event_ms').market_id.astype(int).tolist();initial=55;rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  tem=ms[cur:cur+sz];cur+=sz;q=z[z.market_id.isin(set(tem))].copy();b={'block':bi,'testMarkets':tem,'placementContinuation':metric(q.next_parent_5s.to_numpy(),q.readiness.to_numpy())}
  qs=pd.qcut(q.readiness.rank(method='first'),4,labels=['Q1','Q2','Q3','Q4']);b['quartiles']=[]
  for x in ['Q1','Q2','Q3','Q4']:
   a=q[qs==x];b['quartiles'].append({'q':x,'n':int(len(a)),'meanReadiness':float(a.readiness.mean()),'nextParent5sRate':float(a.next_parent_5s.mean()),'repairRate':float(a.repair.mean())})
  # Directional relation to Formation mode only for attribution, not the readiness semantic target.
  b['readinessVsRepairAuc']=float(roc_auc_score(q.repair,q.readiness))
  blocks.append(b)
 vals=[b['placementContinuation'] for b in blocks];summary={'meanAuc':float(np.mean([x['auc'] for x in vals])),'worstAuc':float(np.min([x['auc'] for x in vals])),'stdAuc':float(np.std([x['auc'] for x in vals])),'meanAp':float(np.mean([x['ap'] for x in vals])),'meanLogLoss':float(np.mean([x['logLoss'] for x in vals])),'allBlocksAucAboveHalf':bool(all(x['auc']>.5 for x in vals)),'readinessVsRepairAucByBlock':[b['readinessVsRepairAuc'] for b in blocks]}
 art={'version':'R4_PLACEMENT_READINESS_ROLE_AUDIT_V1','researchOnly':True,'runtimePromotionAllowed':False,'question':'Does the frozen earlier-cohort 5s placement-readiness belief retain its semantic role on the later 107-market Target Formation cohort, even if its relation to ADD/REPAIR mode drifts?','temporalGuard':{'sourceMaxMs':int(p.decision_sampled_at_ms.max()),'laterMinMs':int(z.first_event_ms.min()),'strictlyEarlier':bool(p.decision_sampled_at_ms.max()<z.first_event_ms.min())},'coverage':{'rows':int(len(z)),'markets':int(z.market_id.nunique())},'blocks':blocks,'summary':summary,'guards':['Readiness model frozen from strictly earlier cohort.','Later next-parent-within-5s outcome is retrospective label only.','No threshold or model tuning.','This audit assigns role, not action authority.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':summary,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
