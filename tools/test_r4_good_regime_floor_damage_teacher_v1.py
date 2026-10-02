from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
from sklearn.tree import DecisionTreeClassifier, export_text

ROOT=Path(__file__).resolve().parents[1]
TRAIN=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_fresh24_v1_rows.csv'
TEST=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_unseen24_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_good_regime_floor_damage_teacher_v1.json'
BINS=['LOW','MID','HIGH'];GOOD={('MID','LOW'),('MID','MID')}
STATE=['seconds_left','floor','absNet','pre_risk_deficit']
CONTEXT=STATE+['predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant']
BELIEFS=CONTEXT+['p_build','p_prepare_role_routed','placement_readiness_native_5s']

def qbin(s):
 r=s.rank(method='first',pct=True)
 return pd.cut(r,[0,1/3,2/3,1.0000001],labels=BINS,include_lowest=True).astype(str)
def prep(d):
 d=d.replace([np.inf,-np.inf],np.nan).dropna(subset=BELIEFS+['floorDelta5s','marketId']).copy()
 d['buildRegime']=qbin(d.p_build);d['prepareRegime']=qbin(d.p_prepare_role_routed)
 d=d[d[['buildRegime','prepareRegime']].apply(tuple,axis=1).isin(GOOD)].copy()
 d['floorDamage5s']=(d.floorDelta5s<0).astype(int)
 return d
def model(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=3,min_samples_leaf=40,l2_regularization=2,max_iter=220,random_state=seed)
def metric(y,p):return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def market_weights(d):
 c=d.groupby('marketId').size();w=d.marketId.map((1/c).to_dict()).astype(float);return (w/w.mean()).to_numpy()
def natural_audit(d):
 out={}
 for c in ['predict_supports_dominant','spot_supports_dominant']:
  z=[]
  for v in [0,1]:
   q=d[d[c].astype(int)==v];z.append({'value':v,'n':int(len(q)),'damageRate':float(q.floorDamage5s.mean())})
  out[c]=z
 for name,mask in [('LATE_0_60',d.seconds_left<60),('MID_60_180',(d.seconds_left>=60)&(d.seconds_left<180)),('EARLY_180_300',d.seconds_left>=180)]:
  q=d[mask];out[name]={'n':int(len(q)),'damageRate':float(q.floorDamage5s.mean())}
 for name,mask in [('PRED_LOW_LT_.10',d.predict_edge<.10),('PRED_MOD_.10_.30',(d.predict_edge>=.10)&(d.predict_edge<.30)),('PRED_EXT_GE_.30',d.predict_edge>=.30)]:
  q=d[mask];out[name]={'n':int(len(q)),'damageRate':float(q.floorDamage5s.mean())}
 return out

def main():
 tr=prep(pd.read_csv(TRAIN));te=prep(pd.read_csv(TEST));w=market_weights(tr)
 sets={'STATE_ONLY':STATE,'STATE_PLUS_PREDICT_STRIKE':CONTEXT,'STATE_PLUS_BELIEFS':BELIEFS};results={};models={}
 for j,(name,feats) in enumerate(sets.items()):
  m=model(20266000+j);m.fit(tr[feats],tr.floorDamage5s,sample_weight=w);models[name]=m;results[name]={'train':metric(tr.floorDamage5s,m.predict_proba(tr[feats])[:,1]),'test':metric(te.floorDamage5s,m.predict_proba(te[feats])[:,1]),'testBlocks':[]}
  mids=sorted(te.marketId.unique().tolist());sizes=[len(mids)//3]*3
  for i in range(len(mids)%3):sizes[i]+=1
  cur=0
  for bi,sz in enumerate(sizes,1):
   mm=mids[cur:cur+sz];cur+=sz;z=te[te.marketId.isin(mm)];results[name]['testBlocks'].append({'block':bi,'markets':list(map(int,mm)),**metric(z.floorDamage5s,m.predict_proba(z[feats])[:,1])})
 base=results['STATE_ONLY']['test']
 for name in ['STATE_PLUS_PREDICT_STRIKE','STATE_PLUS_BELIEFS']:
  r=results[name]['test'];r['deltaAucVsState']=r['auc']-base['auc'];r['deltaApVsState']=r['ap']-base['ap'];r['logLossImprovementVsState']=base['logLoss']-r['logLoss']
 # Simple tree for readable precursor audit; training cohort only.
 tree=DecisionTreeClassifier(max_depth=3,min_samples_leaf=100,class_weight='balanced',random_state=20266010);tree.fit(tr[BELIEFS],tr.floorDamage5s,sample_weight=w)
 tm=metric(te.floorDamage5s,tree.predict_proba(te[BELIEFS])[:,1]);imps=sorted([{'feature':f,'importance':float(v)} for f,v in zip(BELIEFS,tree.feature_importances_)],key=lambda x:-x['importance'])
 art={'version':'R4_GOOD_REGIME_FLOOR_DAMAGE_TEACHER_V1','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,'question':'Within previously identified cross-cohort favorable relative belief regimes BUILD=MID and PREPARE=LOW/MID, can strict-past state predict the residual event floorDelta5s<0 from FRESH24 into untouched UNSEEN24?','coverage':{'trainRows':int(len(tr)),'trainMarkets':int(tr.marketId.nunique()),'trainDamage':int(tr.floorDamage5s.sum()),'trainDamageRate':float(tr.floorDamage5s.mean()),'testRows':int(len(te)),'testMarkets':int(te.marketId.nunique()),'testDamage':int(te.floorDamage5s.sum()),'testDamageRate':float(te.floorDamage5s.mean())},'featureSets':sets,'results':results,'naturalAudit':{'train':natural_audit(tr),'test':natural_audit(te)},'shallowTree':{'test':tm,'featureImportances':imps,'rules':export_text(tree,feature_names=BELIEFS,decimals=4)},'guards':['Good regimes are inherited from prior outcome-blind tertile map; no belief threshold search here.','Damage label floorDelta5s<0 is retrospective scoring only.','Training only on FRESH24; UNSEEN24 never used for fitting.','Rows are market-balanced in training to reduce long-market dominance.','No action changes and no runtime threshold promotion.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'results':results,'naturalAudit':art['naturalAudit'],'shallowTree':art['shallowTree']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
