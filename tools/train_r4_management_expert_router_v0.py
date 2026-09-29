from __future__ import annotations
import json, math, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, balanced_accuracy_score, recall_score
from sklearn.preprocessing import label_binarize, StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
import torch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base

SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_expert_router_v0_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_expert_router_v0.json'
CLASSES=base.CLASSES; F=base.FEATURES; SEED=26082761
SEM=['seconds_left','risk_deficit','abs_gap','weak_active_owners','dominant_active_owners','current_mode_age_s','transitions_15s']

def metrics(y,p):
    y=np.asarray(y,dtype=str);p=np.asarray(p,float);p=np.clip(p,1e-7,1-1e-7);p=p/p.sum(1,keepdims=True)
    pred=np.asarray(CLASSES)[np.argmax(p,axis=1)];Y=label_binarize(y,classes=CLASSES)
    auc=float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro'))
    ap=float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)]))
    rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0)
    return {'n':int(len(y)),'macroAuc':auc,'macroAp':ap,'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)}}

def align(p,classes):
    co=list(classes);return np.column_stack([p[:,co.index(c)] for c in CLASSES])

def ebm(seed):
    return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)

def lgb(seed):
    return LGBMClassifier(objective='multiclass',num_class=3,n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1.,random_state=seed,verbosity=-1,n_jobs=-1)

def predict_tf(model,x,device):
    model.eval()
    with torch.no_grad():return torch.softmax(model(torch.from_numpy(x).to(device)),1).cpu().numpy()

def entropy(p):
    q=np.clip(p,1e-7,1);return -np.sum(q*np.log(q),axis=1)

def router_x(df,pa,pt):
    # Reliability-only signals plus a small semantic context. The target remains expert reliability, never management class.
    ea=entropy(pa);et=entropy(pt);diff=np.abs(pa-pt);agree=(np.argmax(pa,1)==np.argmax(pt,1)).astype(float)
    basecols=np.column_stack([pa,pt,pa.max(1),pt.max(1),ea,et,diff,agree])
    sem=df[SEM].to_numpy(float)
    return np.column_stack([basecols,sem])

def expert_win_target(y,pa,pt):
    idx=np.asarray([CLASSES.index(str(v)) for v in y],int);rows=np.arange(len(idx))
    nla=-np.log(np.clip(pa[rows,idx],1e-7,1));nlt=-np.log(np.clip(pt[rows,idx],1e-7,1))
    return (nlt<nla).astype(int)

def split_expert_router(trm):
    cut=max(1,int(len(trm)*.80));return list(trm[:cut]),list(trm[cut:])

def split_tf_fit_val(mids):
    cut=max(1,int(len(mids)*.85));
    if cut>=len(mids):cut=max(1,len(mids)-1)
    return list(mids[:cut]),list(mids[cut:])

def eligible(df):return df[(df.build_now==1)&df.management_label_5s.notna()&(df.management_label_5s!='')].copy()

def main():
    prereg=json.loads(PREREG.read_text(encoding='utf-8'))
    base.seed_all(SEED)
    d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan)
    d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=F).copy().sort_values(['market_id','t']).reset_index(drop=True)
    ms=base.market_order(d);outer=base.blocks(ms);device='cuda' if torch.cuda.is_available() else 'cpu';blocks=[]
    for bi,trm,tem in outer:
        expert_m,router_m=split_expert_router(trm)
        tf_train_m,tf_val_m=split_tf_fit_val(expert_m)
        expert_all=d[d.market_id.isin(expert_m)].copy();router_all=d[d.market_id.isin(router_m)].copy();test_all=d[d.market_id.isin(tem)].copy()
        ex=eligible(expert_all);rr=eligible(router_all);te=eligible(test_all)
        if len(router_m)<5 or len(rr)<50 or len(te)<50:continue
        # current-state anchor
        em=ebm(SEED+bi*10+1).fit(ex[F],ex.management_label_5s)
        lm=lgb(SEED+bi*10+2).fit(ex[F],ex.management_label_5s)
        pe_r=align(em.predict_proba(rr[F]),em.classes_);pl_r=align(lm.predict_proba(rr[F]),lm.classes_);pa_r=.5*(pe_r+pl_r)
        pe_t=align(em.predict_proba(te[F]),em.classes_);pl_t=align(lm.predict_proba(te[F]),lm.classes_);pa_t=.5*(pe_t+pl_t)
        # lifecycle expert with inner chronology early-stop only
        scaler_all=d[d.market_id.isin(expert_m)].copy();mu,sd=base.fit_scaler(scaler_all)
        tf_train_all=d[d.market_id.isin(tf_train_m)].copy();tf_val_all=d[d.market_id.isin(tf_val_m)].copy()
        tf_train=eligible(tf_train_all);tf_val=eligible(tf_val_all)
        xtr,ytr,_,_=base.build_seq(tf_train_all,tf_train,mu,sd);xva,yva,_,_=base.build_seq(tf_val_all,tf_val,mu,sd)
        xrr,yrr,_,_=base.build_seq(router_all,rr,mu,sd);xte,yte,_,_=base.build_seq(test_all,te,mu,sd)
        base.seed_all(SEED+bi*10+3);tm=base.TinyTransformer(len(F),len(CLASSES));_,eps=base.train_torch(tm,xtr,ytr,xva,yva,device)
        pt_r=predict_tf(tm,xrr,device);pt_t=predict_tf(tm,xte,device)
        # sequence builders preserve eligible row order within market; align frames to that same market-time order
        rr_seq=rr.sort_values(['market_id','t']).reset_index(drop=True);te_seq=te.sort_values(['market_id','t']).reset_index(drop=True)
        # current anchor predictions must follow same order
        pe_r=align(em.predict_proba(rr_seq[F]),em.classes_);pl_r=align(lm.predict_proba(rr_seq[F]),lm.classes_);pa_r=.5*(pe_r+pl_r)
        pe_t=align(em.predict_proba(te_seq[F]),em.classes_);pl_t=align(lm.predict_proba(te_seq[F]),lm.classes_);pa_t=.5*(pe_t+pl_t)
        yr=rr_seq.management_label_5s.astype(str).to_numpy();yt=te_seq.management_label_5s.astype(str).to_numpy()
        if len(pt_r)!=len(rr_seq) or len(pt_t)!=len(te_seq):raise RuntimeError('sequence alignment mismatch')
        win=expert_win_target(yr,pa_r,pt_r)
        # Router predicts which expert wins; no direct class target.
        rx=router_x(rr_seq,pa_r,pt_r);tx=router_x(te_seq,pa_t,pt_t)
        router=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=1000,class_weight='balanced',random_state=SEED+bi*10+4))
        if len(np.unique(win))<2:raise RuntimeError('router target single class')
        router.fit(rx,win);g=router.predict_proba(tx)[:,1]
        pr=(1-g[:,None])*pa_t+g[:,None]*pt_t
        fixed=.65*pa_t+.35*pt_t
        b={'block':bi,'expertFitMarkets':len(expert_m),'routerFitMarkets':len(router_m),'outerTestMarkets':len(tem),'expertRows':int(len(ex)),'routerRows':int(len(rr_seq)),'testRows':int(len(te_seq)),'transformerEpochs':int(eps),'routerLifecycleWinRateTrain':float(win.mean()),'routerMeanLifecycleWeightTest':float(g.mean()),'routerWeightP10':float(np.quantile(g,.1)),'routerWeightP90':float(np.quantile(g,.9)),'STATE_ANCHOR':metrics(yt,pa_t),'LIFECYCLE_EXPERT':metrics(yt,pt_t),'FIXED_65_35':metrics(yt,fixed),'LEARNED_ROUTER':metrics(yt,pr)}
        blocks.append(b);print(json.dumps({'block':bi,'routerWeight':b['routerMeanLifecycleWeightTest'],'anchor':b['STATE_ANCHOR'],'fixed':b['FIXED_65_35'],'router':b['LEARNED_ROUTER']},ensure_ascii=False),flush=True)
    names=['STATE_ANCHOR','LIFECYCLE_EXPERT','FIXED_65_35','LEARNED_ROUTER'];summary={}
    for n in names:
        q=[b[n] for b in blocks]
        summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'stdMacroAuc':float(np.std([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanRecall':{c:float(np.mean([x['perClassRecall'][c] for x in q])) for c in CLASSES}}
    a=summary['STATE_ANCHOR'];r=summary['LEARNED_ROUTER']
    keep=(r['meanMacroAuc']>a['meanMacroAuc'] and r['meanLogLoss']<a['meanLogLoss'] and r['worstMacroAuc']>=a['worstMacroAuc']-.005 and (r['meanRecall']['HANDOFF_ALLOW']>a['meanRecall']['HANDOFF_ALLOW'] or r['meanRecall']['OBSERVE_NO_EVENT']>a['meanRecall']['OBSERVE_NO_EVENT']) and not (r['meanRecall']['HANDOFF_ALLOW']<a['meanRecall']['HANDOFF_ALLOW'] and r['meanRecall']['OBSERVE_NO_EVENT']<a['meanRecall']['OBSERVE_NO_EVENT']))
    art={'version':'R4_MANAGEMENT_EXPERT_ROUTER_V0','researchOnly':True,'actionAuthority':False,'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'coverage':{'source':str(SRC.relative_to(ROOT)).replace('\\','/'),'markets':int(d.market_id.nunique()),'outerBlocks':len(blocks),'device':device},'routerSemantics':'Predict lifecycle-expert-vs-state-anchor reliability, then use router probability as mixture weight. Router never receives management class as its target.','summary':summary,'blocks':blocks,'status':'TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED','fixedRulePassed':bool(keep),'guards':prereg['guards']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':art['status'],'summary':summary},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
