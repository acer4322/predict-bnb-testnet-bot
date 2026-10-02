from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_semantic_stacking_arbiter_v1_oof_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_decomposed_residual_v1.json'
C=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']; A=.15; EPS=1e-7

def norm(p): p=np.clip(np.asarray(p,float),EPS,None);return p/p.sum(1,keepdims=True)
def met(y,p):
 p=norm(p);y=np.asarray(y);Y=label_binarize(y,classes=C);pred=np.asarray(C)[p.argmax(1)];rec=recall_score(y,pred,labels=C,average=None,zero_division=0)
 return {'n':len(y),'macroAuc':float(roc_auc_score(y,p,labels=C,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,p,labels=C)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'recall':{c:float(v) for c,v in zip(C,rec)}}
def mll(df,p):
 vals=[];x=df.reset_index(drop=True)
 for _,g in x.groupby('market_id',sort=False):vals.append(log_loss(g.label,p[g.index],labels=C))
 return float(np.mean(vals))
def variants(d):
 f=d[[f'FLAT_{c}' for c in C]].to_numpy(float); f=norm(f)
 ft=np.clip(f[:,1]+f[:,2],EPS,1-EPS); fh=np.clip(f[:,1]/ft,EPS,1-EPS)
 st=np.clip(d.p_transition_avg3.to_numpy(float),EPS,1-EPS); sh=np.clip(d.p_handoff_avg3.to_numpy(float),EPS,1-EPS)
 # stage1 only: change total transition mass, preserve flat H/O conditional ratio
 t1=(1-A)*ft+A*st; p1=np.column_stack([1-t1,t1*fh,t1*(1-fh)])
 # stage2 only: preserve flat total transition mass exactly, only adjust H/O conditional ratio
 h2=(1-A)*fh+A*sh; p2=np.column_stack([1-ft,ft*h2,ft*(1-h2)])
 # decomposed both: apply both small residuals independently
 pb=np.column_stack([1-t1,t1*h2,t1*(1-h2)])
 # stage2 specialist full replacement diagnostic (no promotion candidate)
 p2full=np.column_stack([1-ft,ft*sh,ft*(1-sh)])
 return {'FLAT_AVG3':f,'STAGE1_RESIDUAL15':p1,'STAGE2_RATIO_RESIDUAL15':p2,'BOTH_DECOMPOSED15':pb,'STAGE2_RATIO_FULL_DIAGNOSTIC':p2full}
def main():
 d=pd.read_csv(SRC); blocks=[]
 for bi in [2,3,4]:
  q=d[d.block==bi].copy().reset_index(drop=True);vv=variants(q);mm={k:{**met(q.label,p),'marketLogLoss':mll(q,norm(p))} for k,p in vv.items()};blocks.append({'block':bi,'rows':len(q),'markets':int(q.market_id.nunique()),'models':mm});print(json.dumps({'block':bi,'models':mm}),flush=True)
 names=list(blocks[0]['models']);summary={}
 for n in names:
  z=[b['models'][n] for b in blocks];summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in z])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in z])),'meanMacroAp':float(np.mean([x['macroAp'] for x in z])),'meanLogLoss':float(np.mean([x['logLoss'] for x in z])),'meanMarketLogLoss':float(np.mean([x['marketLogLoss'] for x in z])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in z])),'meanRecall':{c:float(np.mean([x['recall'][c] for x in z])) for c in C}}
 ref=summary['FLAT_AVG3'];cmp={}
 for n in names[1:]:
  x=summary[n];cmp[n]={'meanMacroAuc':x['meanMacroAuc']-ref['meanMacroAuc'],'worstMacroAuc':x['worstMacroAuc']-ref['worstMacroAuc'],'macroAp':x['meanMacroAp']-ref['meanMacroAp'],'logLossImprovement':ref['meanLogLoss']-x['meanLogLoss'],'marketLogLossImprovement':ref['meanMarketLogLoss']-x['meanMarketLogLoss'],'balancedAccuracy':x['meanBalancedAccuracy']-ref['meanBalancedAccuracy'],'continueRecall':x['meanRecall']['CONTINUE_WEAK']-ref['meanRecall']['CONTINUE_WEAK'],'handoffRecall':x['meanRecall']['HANDOFF_ALLOW']-ref['meanRecall']['HANDOFF_ALLOW'],'observeRecall':x['meanRecall']['OBSERVE_NO_EVENT']-ref['meanRecall']['OBSERVE_NO_EVENT']}
 def passed(n):
  x=cmp[n];return x['meanMacroAuc']>0 and x['worstMacroAuc']>=0 and x['logLossImprovement']>0 and x['handoffRecall']>=0 and x['observeRecall']>=0
 keep=[n for n in ['STAGE1_RESIDUAL15','STAGE2_RATIO_RESIDUAL15','BOTH_DECOMPOSED15'] if passed(n)]
 art={'version':'R4_MANAGEMENT_DECOMPOSED_RESIDUAL_V1','researchOnly':True,'actionAuthority':False,'design':{'alpha':A,'stage1':'adjust total transition mass only; preserve flat H/O ratio','stage2':'preserve flat transition mass exactly; adjust only H/O conditional ratio','noSweep':True},'coverage':{'evaluationBlocks':[2,3,4],'markets':sum(b['markets'] for b in blocks),'rows':sum(b['rows'] for b in blocks)},'summary':summary,'vsFlat':cmp,'passedKeepRule':keep,'decision':'KEEP_'+keep[0] if keep else 'NO_DECOMPOSED_RESIDUAL_PROMOTION','blocks':blocks,'guards':['Fixed 15% residual inherited from prior conservative specialist test; no weight sweep.','All specialist probabilities are strict OOS predictions.','Stage2-only candidate cannot alter total transition probability.','Research only; no runtime modification.']};OUT.write_text(json.dumps(art,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'vsFlat':cmp,'passedKeepRule':keep},indent=2))
if __name__=='__main__':main()
