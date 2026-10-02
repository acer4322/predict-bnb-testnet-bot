from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_semantic_stacking_arbiter_v1_oof_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_management_structural_specialist_gate_v1.json'
C=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT'];A=.15;EPS=1e-7

def norm(p):p=np.clip(np.asarray(p,float),EPS,None);return p/p.sum(1,keepdims=True)
def met(y,p):
 p=norm(p);y=np.asarray(y);Y=label_binarize(y,classes=C);pred=np.asarray(C)[p.argmax(1)];r=recall_score(y,pred,labels=C,average=None,zero_division=0)
 return {'n':len(y),'macroAuc':float(roc_auc_score(y,p,labels=C,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,p,labels=C)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'recall':{c:float(v) for c,v in zip(C,r)}}
def mll(d,p):
 vals=[];q=d.reset_index(drop=True)
 for _,g in q.groupby('market_id',sort=False):vals.append(log_loss(g.label,p[g.index],labels=C))
 return float(np.mean(vals))
def base(d):return norm(d[[f'FLAT_{c}' for c in C]].to_numpy(float))
def stage2_adjust(f,d,alpha=1.0):
 ft=np.clip(f[:,1]+f[:,2],EPS,1-EPS);fh=np.clip(f[:,1]/ft,EPS,1-EPS);sh=np.clip(d.p_handoff_avg3.to_numpy(float),EPS,1-EPS);h=(1-alpha)*fh+alpha*sh;return np.column_stack([1-ft,ft*h,ft*(1-h)])
def variants(d):
 f=base(d);p15=stage2_adjust(f,d,A);pfull=stage2_adjust(f,d,1.0);arg=f.argmax(1);spec_h=(d.p_handoff_avg3.to_numpy(float)>=.5);flat_h=(f[:,1]>=f[:,2]);trans=arg!=0;disagree_all=d.argmax_agree_all.to_numpy(float)<.5;anchor_disagree=d.ebm_lgb_agree.to_numpy(float)<.5;split_disagree=flat_h!=spec_h
 out={'FLAT_AVG3':f}
 for name,mask,src in [
  ('TRANSITION_ONLY_STAGE2_15',trans,p15),
  ('TRANSITION_ONLY_STAGE2_FULL',trans,pfull),
  ('TRANSITION_SPLIT_DISAGREE_FULL',trans & split_disagree,pfull),
  ('ALL_MODEL_DISAGREE_STAGE2_FULL',disagree_all & trans,pfull),
  ('ANCHOR_DISAGREE_STAGE2_FULL',anchor_disagree & trans,pfull),
 ]:
  p=f.copy();p[mask]=src[mask];out[name]=p
 return out
def main():
 d=pd.read_csv(SRC);blocks=[]
 for bi in [2,3,4]:
  q=d[d.block==bi].copy().reset_index(drop=True);vv=variants(q);mm={k:{**met(q.label,p),'marketLogLoss':mll(q,norm(p))} for k,p in vv.items()};blocks.append({'block':bi,'rows':len(q),'markets':int(q.market_id.nunique()),'gateRates':{'flatTransition':float((base(q).argmax(1)!=0).mean()),'allModelDisagree':float((q.argmax_agree_all<.5).mean()),'anchorDisagree':float((q.ebm_lgb_agree<.5).mean())},'models':mm});print(json.dumps({'block':bi,'models':mm}),flush=True)
 names=list(blocks[0]['models']);summary={}
 for n in names:
  z=[b['models'][n] for b in blocks];summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in z])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in z])),'meanMacroAp':float(np.mean([x['macroAp'] for x in z])),'meanLogLoss':float(np.mean([x['logLoss'] for x in z])),'meanMarketLogLoss':float(np.mean([x['marketLogLoss'] for x in z])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in z])),'meanRecall':{c:float(np.mean([x['recall'][c] for x in z])) for c in C}}
 ref=summary['FLAT_AVG3'];cmp={}
 for n in names[1:]:
  x=summary[n];cmp[n]={'meanMacroAuc':x['meanMacroAuc']-ref['meanMacroAuc'],'worstMacroAuc':x['worstMacroAuc']-ref['worstMacroAuc'],'macroAp':x['meanMacroAp']-ref['meanMacroAp'],'logLossImprovement':ref['meanLogLoss']-x['meanLogLoss'],'marketLogLossImprovement':ref['meanMarketLogLoss']-x['meanMarketLogLoss'],'balancedAccuracy':x['meanBalancedAccuracy']-ref['meanBalancedAccuracy'],'handoffRecall':x['meanRecall']['HANDOFF_ALLOW']-ref['meanRecall']['HANDOFF_ALLOW'],'observeRecall':x['meanRecall']['OBSERVE_NO_EVENT']-ref['meanRecall']['OBSERVE_NO_EVENT']}
 def passed(n):
  z=cmp[n];return z['meanMacroAuc']>0 and z['worstMacroAuc']>=0 and z['logLossImprovement']>0 and z['handoffRecall']>=0 and z['observeRecall']>=0
 keep=[n for n in names[1:] if passed(n)]
 art={'version':'R4_MANAGEMENT_STRUCTURAL_SPECIALIST_GATE_V1','researchOnly':True,'actionAuthority':False,'design':{'principle':'specialist cannot change CONTINUE decisions; it only edits HANDOFF/OBSERVE split under natural structural gates','noNumericThresholdSweep':True,'alpha15Inherited':A},'coverage':{'blocks':[2,3,4],'markets':sum(b['markets'] for b in blocks),'rows':sum(b['rows'] for b in blocks)},'summary':summary,'vsFlat':cmp,'passedKeepRule':keep,'decision':'KEEP_'+keep[0] if keep else 'NO_STRUCTURAL_GATE_PROMOTION','blocks':blocks,'guards':['No learned router.','No threshold sweep; gates use argmax/agreement structure only.','Specialist beliefs are strict chronological OOS.','Research only; no runtime modification.']};OUT.write_text(json.dumps(art,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'vsFlat':cmp,'passedKeepRule':keep},indent=2))
if __name__=='__main__':main()
