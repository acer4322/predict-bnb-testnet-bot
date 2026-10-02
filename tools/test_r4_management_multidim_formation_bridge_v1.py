from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_router_v0_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_multidim_formation_bridge_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_multidim_formation_bridge_v1_rows.csv'
PORT=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','placement_readiness_native_5s']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
ROUTED=PORT+RESP+MEM
FORM=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant','placement_readiness_native_5s']
RAW=ROUTED+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))}
def summ(bs,k):
 x=[b[k] for b in bs];return {'meanAuc':float(np.mean([q['auc'] for q in x])),'worstAuc':float(np.min([q['auc'] for q in x])),'stdAuc':float(np.std([q['auc'] for q in x])),'meanAp':float(np.mean([q['ap'] for q in x])),'worstAp':float(np.min([q['ap'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'worstLogLoss':float(np.max([q['logLoss'] for q in x]))}
def main():
 allrows=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan).dropna(subset=list(dict.fromkeys(FORM+RAW+['market_id','t','build_now','need_weak_5s','continue_weak_5s']))).copy();allrows.market_id=allrows.market_id.astype(int)
 d=allrows[(allrows.seconds_left>=60)&(allrows.seconds_left<=300)&(allrows.build_now==1)].copy().sort_values(['market_id','t'])
 ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=18;rem=len(ms)-initial;sizes=[rem//3]*3
 for i in range(rem%3):sizes[i]+=1
 cur=initial;blocks=[];outs=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)].copy();te=d[d.market_id.isin(tem)].copy();fr=allrows[allrows.market_id.isin(trm)].copy()
  fb=hgb(1100+bi).fit(fr[FORM],fr.build_now.astype(int));fn=hgb(1200+bi).fit(fr[FORM],fr.need_weak_5s.astype(int))
  for q in (tr,te):
   q['formation_build_conf']=fb.predict_proba(q[FORM])[:,1];q['formation_need_weak_conf']=fn.predict_proba(q[FORM])[:,1]
  specs=[('ROUTED',ROUTED),('PLUS_BUILD_BELIEF',ROUTED+['formation_build_conf']),('PLUS_NEED_BELIEF',ROUTED+['formation_need_weak_conf']),('MULTI_FORMATION_BELIEF',ROUTED+['formation_build_conf','formation_need_weak_conf']),('RAW_CONTEXT_DIAGNOSTIC',RAW)]
  b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'rows':int(len(te))}
  for j,(name,feats) in enumerate(specs):
   m=hgb(2000+bi*20+j).fit(tr[feats],tr.continue_weak_5s.astype(int));p=m.predict_proba(te[feats])[:,1];b[name]=met(te.continue_weak_5s,p);te['p_'+name.lower()]=p
  blocks.append(b);outs.append(te)
 o=pd.concat(outs,ignore_index=True);o.to_csv(ROWS,index=False);keys=['ROUTED','PLUS_BUILD_BELIEF','PLUS_NEED_BELIEF','MULTI_FORMATION_BELIEF','RAW_CONTEXT_DIAGNOSTIC'];s={k:summ(blocks,k) for k in keys}
 base=s['ROUTED'];multi=s['MULTI_FORMATION_BELIEF'];raw=s['RAW_CONTEXT_DIAGNOSTIC'];s['multiVsRouted']={'meanAuc':multi['meanAuc']-base['meanAuc'],'worstAuc':multi['worstAuc']-base['worstAuc'],'meanAp':multi['meanAp']-base['meanAp'],'logLossImprovement':base['meanLogLoss']-multi['meanLogLoss']};s['remainingGapToRaw']={'meanAuc':raw['meanAuc']-multi['meanAuc'],'worstAuc':raw['worstAuc']-multi['worstAuc'],'meanAp':raw['meanAp']-multi['meanAp'],'logLoss':multi['meanLogLoss']-raw['meanLogLoss']}
 art={'version':'R4_MANAGEMENT_MULTIDIM_FORMATION_BRIDGE_V1','researchOnly':True,'actionAuthority':False,'curriculum':'M0_RESPONSIBILITY_CONTINUATION','question':'Can separate semantic Formation beliefs for current BUILD state and near-term weak-responsibility need bridge raw Formation context into the Manager without exposing raw Predict/strike features?','coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'phase':'60-300s current BUILD'},'semanticBeliefs':{'formation_build_conf':'separate chronological Formation head predicts current BUILD','formation_need_weak_conf':'separate chronological Formation head predicts any weak responsibility within 5s'},'summary':s,'blocks':blocks,'rowsArtifact':str(ROWS.relative_to(ROOT)).replace('\\','/'),'guards':['Both semantic beliefs are generated out-of-block from Formation-only context; future labels are training labels only.','Manager continuation head receives beliefs plus portfolio/responsibility/memory; raw Predict/strike remains diagnostic-only.','No threshold sweep, no action authority, no winner/settlement.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':s,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
