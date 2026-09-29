from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_m1_role_routed_v2.json'
PRE=ROOT/'data/research/r4_v0/hourly/r4_management_m1_role_routed_v2_preregistered.json'
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s'];FULL=BASE+PROG
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
def ebm(seed,features):return ExplainableBoostingClassifier(feature_names=features,max_bins=64,max_interaction_bins=16,interactions=4,outer_bags=4,learning_rate=.035,max_rounds=700,early_stopping_rounds=50,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def met(y,p):
 y=np.asarray(y);p=np.asarray(p,float);p=np.clip(p,1e-7,1-1e-7);p=p/p.sum(axis=1,keepdims=True);Y=label_binarize(y,classes=CLASSES);pred=np.asarray(CLASSES)[np.argmax(p,axis=1)];rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0);return {'n':int(len(y)),'macroAuc':float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'recall':{c:float(v) for c,v in zip(CLASSES,rec)}}
def align(p,classes):return np.column_stack([p[:,list(classes).index(c)] for c in CLASSES])
def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);d=d[(d.seconds_left>=60)&(d.seconds_left<=300)&(d.build_now==1)&d.management_label_5s.notna()&(d.management_label_5s!='')].dropna(subset=FULL).copy().sort_values(['market_id','t']);d['transition5']=(d.management_label_5s!='CONTINUE_WEAK').astype(int)
 ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=min(200,max(120,int(len(ms)*2/3)));rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)]
  mono=ebm(33000+bi,FULL).fit(tr[FULL],tr.management_label_5s);pm=align(mono.predict_proba(te[FULL]),mono.classes_)
  gate=ebm(33100+bi,FULL).fit(tr[FULL],tr.transition5);pt=gate.predict_proba(te[FULL])[:,list(gate.classes_).index(1)]
  tr2=tr[tr.transition5==1].copy();tr2['handoff']=(tr2.management_label_5s=='HANDOFF_ALLOW').astype(int);split=ebm(33200+bi,BASE).fit(tr2[BASE],tr2.handoff);ph=split.predict_proba(te[BASE])[:,list(split.classes_).index(1)]
  pr=np.column_stack([1-pt,pt*ph,pt*(1-ph)])
  blocks.append({'block':bi,'testMarkets':len(tem),'testRows':int(len(te)),'MONOLITHIC_EBM_PROGRESS':met(te.management_label_5s,pm),'ROLE_ROUTED_V2':met(te.management_label_5s,pr)})
 def summ(k):
  q=[b[k] for b in blocks];return {'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanRecall':{c:float(np.mean([x['recall'][c] for x in q])) for c in CLASSES}}
 s={k:summ(k) for k in ['MONOLITHIC_EBM_PROGRESS','ROLE_ROUTED_V2']};a=s['MONOLITHIC_EBM_PROGRESS'];r=s['ROLE_ROUTED_V2'];delta={'meanMacroAuc':r['meanMacroAuc']-a['meanMacroAuc'],'worstMacroAuc':r['worstMacroAuc']-a['worstMacroAuc'],'meanMacroAp':r['meanMacroAp']-a['meanMacroAp'],'logLossImprovement':a['meanLogLoss']-r['meanLogLoss'],'balancedAccuracyDelta':r['meanBalancedAccuracy']-a['meanBalancedAccuracy']};passed=delta['meanMacroAuc']>=0 and delta['worstMacroAuc']>=0 and delta['meanMacroAp']>=0 and delta['logLossImprovement']>=0 and delta['balancedAccuracyDelta']>=-.005
 art={'version':'R4_MANAGEMENT_M1_ROLE_ROUTED_V2','researchOnly':True,'actionAuthority':False,'preRegistration':str(PRE.relative_to(ROOT)).replace('\\','/'),'coverage':{'rows':int(len(d)),'markets':int(d.market_id.nunique()),'blocks':len(blocks)},'summary':s,'delta':delta,'status':'TESTED_KEEP_SIGNAL' if passed else 'TESTED_REJECTED','fixedRulePassed':passed,'blocks':blocks,'guards':['No threshold/weight sweep.','Same chronological blocks.','Strict-past state only.','No action authority.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':art['status'],'summary':s,'delta':delta},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
