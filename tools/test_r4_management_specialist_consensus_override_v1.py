from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_semantic_stacking_arbiter_v1_oof_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_management_specialist_consensus_override_v1.json'
C=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT'];EPS=1e-7

def norm(p):p=np.clip(np.asarray(p,float),EPS,None);return p/p.sum(1,keepdims=True)
def met(y,p):
 p=norm(p);y=np.asarray(y);Y=label_binarize(y,classes=C);pred=np.asarray(C)[p.argmax(1)];r=recall_score(y,pred,labels=C,average=None,zero_division=0)
 return {'n':len(y),'macroAuc':float(roc_auc_score(y,p,labels=C,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,p,labels=C)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'recall':{c:float(v) for c,v in zip(C,r)}}
def mll(d,p):
 vals=[];q=d.reset_index(drop=True)
 for _,g in q.groupby('market_id',sort=False):vals.append(log_loss(g.label,p[g.index],labels=C))
 return float(np.mean(vals))
def flat(d):return norm(d[[f'FLAT_{c}' for c in C]].to_numpy(float))
def hier(d):
 pt=np.clip(d.p_transition_avg3.to_numpy(float),EPS,1-EPS);ph=np.clip(d.p_handoff_avg3.to_numpy(float),EPS,1-EPS);return np.column_stack([1-pt,pt*ph,pt*(1-ph)])
def variants(d):
 f=flat(d);h=hier(d);fa=f.argmax(1)
 tv=np.column_stack([d.p_transition_ebm,d.p_transition_lgb,d.p_transition_tf]).astype(float)>=.5
 hv=np.column_stack([d.p_handoff_ebm,d.p_handoff_lgb,d.p_handoff_tf]).astype(float)>=.5
 all_trans=tv.all(1); majority_trans=tv.sum(1)>=2; all_h=hv.all(1); all_o=(~hv).all(1); split_consensus=all_h|all_o
 # High-consensus rescue only: flat CONTINUE can be overridden only when transition specialists agree.
 out={'FLAT_AVG3':f}
 for name,trans_mask,need_split in [
  ('ALL3_TRANSITION_RESCUE',all_trans,False),
  ('ALL3_TRANSITION_AND_SPLIT_CONSENSUS_RESCUE',all_trans,True),
  ('MAJORITY_TRANSITION_AND_SPLIT_CONSENSUS_RESCUE',majority_trans,True),
 ]:
  mask=(fa==0)&trans_mask
  if need_split:mask=mask&split_consensus
  p=f.copy();p[mask]=h[mask];out[name]=p
 # symmetric conservative correction: only all-three transition consensus can override CONTINUE, and all-three continue consensus can override a flat transition.
 all_continue=(~tv).all(1);mask_up=(fa==0)&all_trans&split_consensus;mask_down=(fa!=0)&all_continue
 p=f.copy();p[mask_up]=h[mask_up];p[mask_down]=h[mask_down];out['SYMMETRIC_ALL3_CONSENSUS']=p
 return out,{'all3Transition':float(all_trans.mean()),'majorityTransition':float(majority_trans.mean()),'splitConsensus':float(split_consensus.mean()),'flatContinue':float((fa==0).mean()),'rescueAll3Split':float(((fa==0)&all_trans&split_consensus).mean())}
def main():
 d=pd.read_csv(SRC);blocks=[]
 for bi in [2,3,4]:
  q=d[d.block==bi].copy().reset_index(drop=True);vv,rates=variants(q);mm={k:{**met(q.label,p),'marketLogLoss':mll(q,norm(p))} for k,p in vv.items()};blocks.append({'block':bi,'rows':len(q),'markets':int(q.market_id.nunique()),'gateRates':rates,'models':mm});print(json.dumps({'block':bi,'gateRates':rates,'models':mm}),flush=True)
 names=list(blocks[0]['models']);summary={}
 for n in names:
  z=[b['models'][n] for b in blocks];summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in z])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in z])),'meanMacroAp':float(np.mean([x['macroAp'] for x in z])),'meanLogLoss':float(np.mean([x['logLoss'] for x in z])),'meanMarketLogLoss':float(np.mean([x['marketLogLoss'] for x in z])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in z])),'meanRecall':{c:float(np.mean([x['recall'][c] for x in z])) for c in C}}
 ref=summary['FLAT_AVG3'];cmp={}
 for n in names[1:]:
  x=summary[n];cmp[n]={'meanMacroAuc':x['meanMacroAuc']-ref['meanMacroAuc'],'worstMacroAuc':x['worstMacroAuc']-ref['worstMacroAuc'],'macroAp':x['meanMacroAp']-ref['meanMacroAp'],'logLossImprovement':ref['meanLogLoss']-x['meanLogLoss'],'marketLogLossImprovement':ref['meanMarketLogLoss']-x['meanMarketLogLoss'],'balancedAccuracy':x['meanBalancedAccuracy']-ref['meanBalancedAccuracy'],'continueRecall':x['meanRecall']['CONTINUE_WEAK']-ref['meanRecall']['CONTINUE_WEAK'],'handoffRecall':x['meanRecall']['HANDOFF_ALLOW']-ref['meanRecall']['HANDOFF_ALLOW'],'observeRecall':x['meanRecall']['OBSERVE_NO_EVENT']-ref['meanRecall']['OBSERVE_NO_EVENT']}
 def passed(n):
  z=cmp[n];return z['meanMacroAuc']>0 and z['worstMacroAuc']>=0 and z['logLossImprovement']>0 and z['handoffRecall']>=0 and z['observeRecall']>=0
 keep=[n for n in names[1:] if passed(n)]
 art={'version':'R4_MANAGEMENT_SPECIALIST_CONSENSUS_OVERRIDE_V1','researchOnly':True,'actionAuthority':False,'design':{'principle':'specialists may override only under natural 0.5-vote consensus; no learned router and no threshold sweep','specialistHierarchy':'transition consensus chooses whether to leave CONTINUE; handoff specialist consensus chooses transition subtype'},'coverage':{'blocks':[2,3,4],'markets':sum(b['markets'] for b in blocks),'rows':sum(b['rows'] for b in blocks)},'summary':summary,'vsFlat':cmp,'passedKeepRule':keep,'decision':'KEEP_'+keep[0] if keep else 'NO_CONSENSUS_OVERRIDE_PROMOTION','blocks':blocks,'guards':['All specialist predictions are strict OOS.','Only natural binary 0.5 votes and unanimity/majority structure used; no numeric tuning.','Research only; no runtime modification.']};OUT.write_text(json.dumps(art,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'vsFlat':cmp,'passedKeepRule':keep},indent=2))
if __name__=='__main__':main()
