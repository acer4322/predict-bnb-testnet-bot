from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler, label_binarize
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, balanced_accuracy_score, recall_score

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_semantic_stacking_arbiter_v1_oof_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_low_capacity_semantic_combiner_v1.json'
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
EPS=1e-6

def metric(y,p):
    y=np.asarray(y); p=np.clip(np.asarray(p,float),1e-7,1-1e-7); p=p/p.sum(1,keepdims=True)
    Y=label_binarize(y,classes=CLASSES); pred=np.asarray(CLASSES)[np.argmax(p,1)]; rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0)
    return {'n':int(len(y)),'macroAuc':float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)}}
def market_ll(df,p):
    vals=[]
    for _,g in df.reset_index(drop=True).groupby('market_id',sort=False):
        vals.append(log_loss(g.label.astype(str),p[g.index.to_numpy()],labels=CLASSES))
    return float(np.mean(vals))
def logits(p):
    p=np.clip(np.asarray(p,float),EPS,1-EPS); return np.log(p/(1-p))
def flat_prob(df): return df[[f'FLAT_{c}' for c in CLASSES]].to_numpy(float)
def hier_prob(df):
    pt=np.clip(df.p_transition_avg3.to_numpy(float),EPS,1-EPS); ph=np.clip(df.p_handoff_avg3.to_numpy(float),EPS,1-EPS)
    p=np.column_stack([1-pt,pt*ph,pt*(1-ph)]); return p/p.sum(1,keepdims=True)
def x_cal(df):
    p=flat_prob(df); return np.log(np.clip(p,EPS,1)).astype(float)
def x_sem(df):
    p=flat_prob(df)
    return np.column_stack([np.log(np.clip(p,EPS,1)),logits(df.p_transition_avg3),logits(df.p_handoff_avg3)])
def x_sem_std(df):
    return np.column_stack([x_sem(df),df.p_transition_std.to_numpy(float),df.p_handoff_std.to_numpy(float)])
def fit_lr(x,y):
    # Strong fixed regularization: deliberately low-capacity stacking, no parameter sweep.
    m=make_pipeline(StandardScaler(),LogisticRegression(C=0.10,max_iter=2000,solver='lbfgs'))
    m.fit(x,y); return m
def align(m,p):
    classes=list(m[-1].classes_); return np.column_stack([p[:,classes.index(c)] for c in CLASSES])

def main():
    d=pd.read_csv(SRC); evals=[]
    for bi in [2,3,4]:
        tr=d[d.block<bi].copy(); te=d[d.block==bi].copy(); pf=flat_prob(te); ph=hier_prob(te)
        cand={'FLAT_AVG3':pf,'FIXED_RESIDUAL_15':.85*pf+.15*ph}
        for name,fx in [('LINEAR_CAL_ONLY',x_cal),('LINEAR_SEMANTIC',x_sem),('LINEAR_SEMANTIC_STD',x_sem_std)]:
            m=fit_lr(fx(tr),tr.label.astype(str)); cand[name]=align(m,m.predict_proba(fx(te)))
        models={}
        for name,p in cand.items():
            models[name]=metric(te.label,p); models[name]['marketLogLoss']=market_ll(te,p)
        evals.append({'block':bi,'trainRows':int(len(tr)),'trainBlocks':sorted(tr.block.unique().astype(int).tolist()),'testRows':int(len(te)),'testMarkets':int(te.market_id.nunique()),'models':models})
        print(json.dumps({'block':bi,'models':models}),flush=True)
    names=['FLAT_AVG3','FIXED_RESIDUAL_15','LINEAR_CAL_ONLY','LINEAR_SEMANTIC','LINEAR_SEMANTIC_STD']; summary={}
    for n in names:
        q=[e['models'][n] for e in evals]; summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanMarketLogLoss':float(np.mean([x['marketLogLoss'] for x in q])),'meanRecall':{c:float(np.mean([x['perClassRecall'][c] for x in q])) for c in CLASSES}}
    ref=summary['FLAT_AVG3']; comparisons={}
    for n in names[1:]:
        x=summary[n]; comparisons[n]={'meanMacroAuc':x['meanMacroAuc']-ref['meanMacroAuc'],'worstMacroAuc':x['worstMacroAuc']-ref['worstMacroAuc'],'macroAp':x['meanMacroAp']-ref['meanMacroAp'],'logLossImprovement':ref['meanLogLoss']-x['meanLogLoss'],'marketLogLossImprovement':ref['meanMarketLogLoss']-x['meanMarketLogLoss'],'balancedAccuracy':x['meanBalancedAccuracy']-ref['meanBalancedAccuracy'],'handoffRecall':x['meanRecall']['HANDOFF_ALLOW']-ref['meanRecall']['HANDOFF_ALLOW'],'observeRecall':x['meanRecall']['OBSERVE_NO_EVENT']-ref['meanRecall']['OBSERVE_NO_EVENT']}
    def pass_rule(n):
        z=comparisons[n]; return bool(z['meanMacroAuc']>0 and z['worstMacroAuc']>=0 and z['logLossImprovement']>0 and z['handoffRecall']>=0 and z['observeRecall']>=0)
    passed=[n for n in names[1:] if pass_rule(n)]
    art={'version':'R4_MANAGEMENT_LOW_CAPACITY_SEMANTIC_COMBINER_V1','researchOnly':True,'actionAuthority':False,'design':{'source':'strict chronological OOS base + specialist beliefs from semantic stacking V1','fixedResidual':'85% flat AVG3 + 15% specialist hierarchical belief','linearModels':'strongly regularized multinomial logistic regression trained only on earlier OOS blocks','noSweep':True},'coverage':{'oofRows':int(len(d)),'evaluationMarkets':int(sum(e['testMarkets'] for e in evals)),'evaluationRows':int(sum(e['testRows'] for e in evals))},'summary':summary,'vsFlat':comparisons,'passedKeepRule':passed,'decision':'KEEP_'+passed[0] if passed else 'NO_LOW_CAPACITY_COMBINER_PROMOTION','blocks':evals,'guards':['No hyperparameter/threshold/weight sweep.','Evaluation block is never used to train its combiner.','Specialist beliefs are OOS from earlier-market-trained experts.','Research only; no runtime/action modification.']}
    OUT.write_text(json.dumps(art,indent=2),encoding='utf-8'); print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'vsFlat':comparisons,'passedKeepRule':passed},indent=2))
if __name__=='__main__': main()
