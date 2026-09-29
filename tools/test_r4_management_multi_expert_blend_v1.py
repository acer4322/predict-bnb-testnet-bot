from __future__ import annotations
import json, random, sys
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, balanced_accuracy_score, recall_score
from sklearn.preprocessing import label_binarize
from lightgbm import LGBMClassifier
import torch
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0,str(HERE))
import train_r4_management_model_benchmark_v1 as base

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_multi_expert_blend_v1.json'
CLASSES=base.CLASSES; FEATURES=base.FEATURES; SEED=26082731

def metric(y,p):
    y=np.asarray(y); p=np.asarray(p,float); p=np.clip(p,1e-8,None); p=p/p.sum(1,keepdims=True)
    pred=np.asarray(CLASSES)[np.argmax(p,1)]; Y=label_binarize(y,classes=CLASSES)
    auc=float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro'))
    aps=[average_precision_score(Y[:,j],p[:,j]) for j in range(3)]
    rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0)
    return {'n':int(len(y)),'macroAuc':auc,'macroAp':float(np.mean(aps)),'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)}}

def norm(p):
    p=np.clip(np.asarray(p,float),1e-8,None); return p/p.sum(1,keepdims=True)

def main():
    base.seed_all(SEED)
    d=pd.read_csv(SRC)
    d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=FEATURES).copy().sort_values(['market_id','t']).reset_index(drop=True)
    ms=base.market_order(d); bspec=base.blocks(ms); device='cuda' if torch.cuda.is_available() else 'cpu'
    results=[]
    for bi,trm,tem in bspec:
        base.seed_all(SEED+bi)
        tr_all=d[d.market_id.isin(trm)].copy(); te_all=d[d.market_id.isin(tem)].copy()
        tr=tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.notna()&(tr_all.management_label_5s!='')].copy()
        te=te_all[(te_all.build_now==1)&te_all.management_label_5s.notna()&(te_all.management_label_5s!='')].copy()
        lgb=LGBMClassifier(objective='multiclass',num_class=3,n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1.0,random_state=SEED+bi,verbosity=-1,n_jobs=-1)
        lgb.fit(tr[FEATURES],tr.management_label_5s)
        pp=lgb.predict_proba(te[FEATURES]); order=list(lgb.classes_); pl=norm(np.column_stack([pp[:,order.index(c)] for c in CLASSES]))
        mu,sd=base.fit_scaler(tr_all); xtr,ytr,_,_=base.build_seq(tr_all,tr,mu,sd); xte,yte,_,_=base.build_seq(te_all,te,mu,sd)
        base.seed_all(SEED+bi+29); pt,eps=base.train_torch(base.TinyTransformer(len(FEATURES),len(CLASSES)),xtr,ytr,xte,yte,device); pt=norm(pt)
        y=np.asarray(CLASSES)[yte]
        variants={
            'LIGHTGBM':pl,
            'TINY_TRANSFORMER':pt,
            'AVG_50':norm(.5*pl+.5*pt),
            'LGBM_65_TF_35':norm(.65*pl+.35*pt),
        }
        role=np.empty_like(pl)
        role[:,0]=.75*pl[:,0]+.25*pt[:,0]
        role[:,1]=.30*pl[:,1]+.70*pt[:,1]
        role[:,2]=.30*pl[:,2]+.70*pt[:,2]
        variants['ROLE_SPECIALIST']=norm(role)
        agree=np.argmax(pl,1)==np.argmax(pt,1)
        pg=np.where(agree[:,None],.5*pl+.5*pt,.70*pl+.30*pt)
        variants['AGREEMENT_GATE']=norm(pg)
        block={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'testRows':len(te),'transformerEpochs':eps,'agreementRate':float(agree.mean())}
        for k,p in variants.items(): block[k]=metric(y,p)
        results.append(block); print(json.dumps({'block':bi,'agreementRate':block['agreementRate'],**{k:block[k] for k in variants}},ensure_ascii=False),flush=True)
    names=['LIGHTGBM','TINY_TRANSFORMER','AVG_50','LGBM_65_TF_35','ROLE_SPECIALIST','AGREEMENT_GATE']
    def summ(name):
        q=[b[name] for b in results]
        return {'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'stdMacroAuc':float(np.std([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanRecall':{c:float(np.mean([x['perClassRecall'][c] for x in q])) for c in CLASSES}}
    summary={n:summ(n) for n in names}
    art={'version':'R4_MANAGEMENT_MULTI_EXPERT_BLEND_V1','researchOnly':True,'actionAuthority':False,'task':'M1 fixed multi-expert blending without threshold search','source':str(SRC.relative_to(ROOT)).replace('\\','/'),'device':device,'experts':['LIGHTGBM current-state','TINY_TRANSFORMER causal 16-state history'],'blendDefinitions':{'AVG_50':'0.5 LGBM + 0.5 TF','LGBM_65_TF_35':'0.65 LGBM + 0.35 TF','ROLE_SPECIALIST':{'CONTINUE_WEAK':'0.75 LGBM + 0.25 TF','HANDOFF_ALLOW':'0.30 LGBM + 0.70 TF','OBSERVE_NO_EVENT':'0.30 LGBM + 0.70 TF'},'AGREEMENT_GATE':'if argmax agrees 0.5/0.5; else 0.70 LGBM + 0.30 TF'},'summary':summary,'blocks':results,'guards':['Weights preregistered in code before observing this run; no threshold/weight sweep.','Same chronological blocks and frozen labels/features as benchmark V1.','No future/winner/settlement inputs.','Research only; no runtime authority.']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'meanAgreementRate':float(np.mean([b['agreementRate'] for b in results]))},ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__': main()
