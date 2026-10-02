from __future__ import annotations
import json, math, random, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, balanced_accuracy_score, recall_score
from sklearn.preprocessing import label_binarize
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base

SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
BASE_OOF=ROOT/'data/research/r4_v0/hourly/r4_management_meta_router_v1_oof_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_semantic_stacking_arbiter_v1.json'
OOF_OUT=ROOT/'data/research/r4_v0/hourly/r4_management_semantic_stacking_arbiter_v1_oof_rows.csv'
CLASSES=base.CLASSES; F=base.FEATURES; SEED=26082791; SEQ_LEN=base.SEQ_LEN

SEMANTIC=['seconds_left','risk_deficit','floor_per_gross','absnet_ratio','weak_active_owners','dominant_active_owners','current_mode_age_s','transitions_15s']
BASE_PROBS=[f'{m}_{c}' for m in ['EBM','LIGHTGBM','TINY_TRANSFORMER'] for c in CLASSES]
BASE_META=['EBM_max','EBM_entropy','LIGHTGBM_max','LIGHTGBM_entropy','TINY_TRANSFORMER_max','TINY_TRANSFORMER_entropy','argmax_agree_all','ebm_lgb_agree','ebm_tf_agree','lgb_tf_agree']
SPEC=['p_transition_ebm','p_transition_lgb','p_transition_tf','p_transition_avg3','p_transition_std','p_handoff_ebm','p_handoff_lgb','p_handoff_tf','p_handoff_avg3','p_handoff_std']


def seed_all(s): base.seed_all(s)

def ebm(features,seed):
    return ExplainableBoostingClassifier(feature_names=features,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)

def binary_seq(all_d, eligible, mu, sd, target_col):
    xs=[]; ys=[]; mids=[]; times=[]
    elig_idx=set(eligible.index.tolist())
    for mid,g in all_d.groupby('market_id',sort=False):
        g=g.sort_values('t'); arr=((g[F].to_numpy(np.float32)-mu)/sd); idxs=g.index.to_numpy()
        for j,idx in enumerate(idxs):
            if idx not in elig_idx: continue
            lo=max(0,j-SEQ_LEN+1); s=arr[lo:j+1]
            if len(s)<SEQ_LEN:
                pad=np.repeat(s[:1],SEQ_LEN-len(s),axis=0) if len(s) else np.zeros((SEQ_LEN,len(F)),np.float32); s=np.concatenate([pad,s],axis=0)
            xs.append(s); ys.append(int(all_d.at[idx,target_col])); mids.append(int(mid)); times.append(int(all_d.at[idx,'t']))
    return np.asarray(xs,np.float32),np.asarray(ys,np.int64),mids,times

def train_binary_tf(xtr,ytr,xva,yva,seed,device,epochs=24):
    seed_all(seed); model=base.TinyTransformer(len(F),2).to(device)
    counts=np.bincount(ytr,minlength=2).astype(np.float32); w=counts.sum()/np.maximum(counts,1); w=w/w.mean()
    lossfn=nn.CrossEntropyLoss(weight=torch.tensor(w,dtype=torch.float32,device=device)); opt=torch.optim.AdamW(model.parameters(),lr=1.5e-3,weight_decay=1e-4)
    dl=DataLoader(TensorDataset(torch.from_numpy(xtr),torch.from_numpy(ytr)),batch_size=128,shuffle=True,num_workers=0)
    best=None; best_loss=1e9; bad=0; patience=5
    for ep in range(epochs):
        model.train()
        for xb,yb in dl:
            xb=xb.to(device); yb=yb.to(device); opt.zero_grad(set_to_none=True); loss=lossfn(model(xb),yb); loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),2.0); opt.step()
        model.eval()
        with torch.no_grad(): pv=torch.softmax(model(torch.from_numpy(xva).to(device)),1).cpu().numpy()
        vl=log_loss(yva,np.clip(pv,1e-7,1-1e-7),labels=[0,1])
        if vl<best_loss-1e-4:
            best_loss=vl; best={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}; bad=0
        else:
            bad+=1
            if bad>=patience: break
    if best is not None: model.load_state_dict(best)
    model.eval()
    with torch.no_grad(): p=torch.softmax(model(torch.from_numpy(xva).to(device)),1).cpu().numpy()[:,1]
    return p,ep+1

def metric(y,p):
    y=np.asarray(y); p=np.clip(np.asarray(p,float),1e-7,1-1e-7); p=p/p.sum(axis=1,keepdims=True)
    Y=label_binarize(y,classes=CLASSES); pred=np.asarray(CLASSES)[np.argmax(p,axis=1)]
    rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0)
    return {'n':int(len(y)),'macroAuc':float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)}}
def market_ll(df,p):
    out=[]
    for _,g in df.reset_index().groupby('market_id'):
        pos=g.index.to_numpy(); out.append(log_loss(g.label.astype(str),p[pos],labels=CLASSES))
    return float(np.mean(out))
def align_prob(model,p):
    order=list(model.classes_); return np.column_stack([p[:,order.index(c)] for c in CLASSES])
def stack_model():
    return HistGradientBoostingClassifier(max_iter=180,learning_rate=.04,max_leaf_nodes=7,min_samples_leaf=28,l2_regularization=2.0,random_state=SEED)

def main():
    seed_all(SEED); device='cuda' if torch.cuda.is_available() else 'cpu'
    d=pd.read_csv(SRC); d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=F).copy().sort_values(['market_id','t']).reset_index(drop=True)
    d['transition']=(d.management_label_5s.notna()&(d.management_label_5s!='')&(d.management_label_5s!='CONTINUE_WEAK')).astype(int)
    d['handoff']=(d.management_label_5s=='HANDOFF_ALLOW').astype(int)
    base_oof=pd.read_csv(BASE_OOF)
    spec_rows=[]; epoch_log=[]
    for bi,trm,tem in base.blocks(base.market_order(d)):
        tr_all=d[d.market_id.isin(trm)].copy(); te_all=d[d.market_id.isin(tem)].copy()
        tr=tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.notna()&(tr_all.management_label_5s!='')].copy()
        te=te_all[(te_all.build_now==1)&te_all.management_label_5s.notna()&(te_all.management_label_5s!='')].copy()
        # Stage 1 transition specialists
        em=ebm(F,SEED+bi).fit(tr[F],tr.transition); pe=em.predict_proba(te[F])[:,list(em.classes_).index(1)]
        lm=LGBMClassifier(n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1,random_state=SEED+bi,verbosity=-1,n_jobs=-1).fit(tr[F],tr.transition); pl=lm.predict_proba(te[F])[:,list(lm.classes_).index(1)]
        mu,sd=base.fit_scaler(tr_all); xtr,ytr,_,_=binary_seq(tr_all,tr,mu,sd,'transition'); xte,yte,mids,times=binary_seq(te_all,te,mu,sd,'transition'); pt,e1=train_binary_tf(xtr,ytr,xte,yte,SEED+bi+31,device)
        # Stage 2 conditional HANDOFF/OBSERVE specialists; predict belief for every M1 test row
        tr2=tr[tr.transition==1].copy()
        em2=ebm(F,SEED+bi+101).fit(tr2[F],tr2.handoff); he=em2.predict_proba(te[F])[:,list(em2.classes_).index(1)]
        lm2=LGBMClassifier(n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=28,subsample=.9,colsample_bytree=.9,reg_lambda=1,random_state=SEED+bi+101,verbosity=-1,n_jobs=-1).fit(tr2[F],tr2.handoff); hl=lm2.predict_proba(te[F])[:,list(lm2.classes_).index(1)]
        xtr2,ytr2,_,_=binary_seq(tr_all,tr2,mu,sd,'handoff'); xte2,yte2,mids2,times2=binary_seq(te_all,te,mu,sd,'handoff'); ht,e2=train_binary_tf(xtr2,ytr2,xte2,yte2,SEED+bi+131,device)
        assert mids==mids2 and times==times2
        q=pd.DataFrame({'market_id':mids,'t':times,'block':bi,'p_transition_ebm':pe,'p_transition_lgb':pl,'p_transition_tf':pt,'p_handoff_ebm':he,'p_handoff_lgb':hl,'p_handoff_tf':ht})
        q['p_transition_avg3']=q[['p_transition_ebm','p_transition_lgb','p_transition_tf']].mean(axis=1); q['p_transition_std']=q[['p_transition_ebm','p_transition_lgb','p_transition_tf']].std(axis=1)
        q['p_handoff_avg3']=q[['p_handoff_ebm','p_handoff_lgb','p_handoff_tf']].mean(axis=1); q['p_handoff_std']=q[['p_handoff_ebm','p_handoff_lgb','p_handoff_tf']].std(axis=1)
        spec_rows.append(q); epoch_log.append({'block':bi,'transitionTfEpochs':e1,'handoffTfEpochs':e2}); print(json.dumps({'block':bi,'rows':len(q),'transitionTfEpochs':e1,'handoffTfEpochs':e2}),flush=True)
    spec=pd.concat(spec_rows,ignore_index=True)
    oof=base_oof.merge(spec,on=['market_id','t','block'],how='inner',validate='one_to_one')
    assert len(oof)==len(base_oof),(len(oof),len(base_oof))
    # fixed flat reference probabilities
    flat=np.mean([oof[[f'{m}_{c}' for c in CLASSES]].to_numpy(float) for m in ['EBM','LIGHTGBM','TINY_TRANSFORMER']],axis=0)
    for j,c in enumerate(CLASSES): oof[f'FLAT_{c}']=flat[:,j]
    oof.to_csv(OOF_OUT,index=False)
    base_stack_feats=SEMANTIC+BASE_PROBS+BASE_META
    semantic_stack_feats=base_stack_feats+SPEC
    belief_only_feats=BASE_PROBS+BASE_META+SPEC
    evals=[]
    for bi in [2,3,4]:
        tr=oof[oof.block<bi].copy(); te=oof[oof.block==bi].copy()
        models={}
        # fixed reference
        pflat=te[[f'FLAT_{c}' for c in CLASSES]].to_numpy(float); models['FLAT_AVG3']=metric(te.label,pflat)
        for name,features in [('STACK_NO_SPECIALISTS',base_stack_feats),('SEMANTIC_STACK',semantic_stack_feats),('BELIEF_ONLY_STACK',belief_only_feats)]:
            sm=stack_model(); sm.fit(tr[features],tr.label); pp=align_prob(sm,sm.predict_proba(te[features])); models[name]=metric(te.label,pp); models[name]['marketLogLoss']=market_ll(te,pp)
        models['FLAT_AVG3']['marketLogLoss']=market_ll(te,pflat)
        evals.append({'block':bi,'trainOofBlocks':sorted(tr.block.unique().astype(int).tolist()),'trainRows':int(len(tr)),'testRows':int(len(te)),'testMarkets':int(te.market_id.nunique()),'models':models})
        print(json.dumps({'block':bi,'models':models}),flush=True)
    names=['FLAT_AVG3','STACK_NO_SPECIALISTS','SEMANTIC_STACK','BELIEF_ONLY_STACK']; summary={}
    for n in names:
        q=[e['models'][n] for e in evals]; summary[n]={'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanMarketLogLoss':float(np.mean([x['marketLogLoss'] for x in q])),'meanRecall':{c:float(np.mean([x['perClassRecall'][c] for x in q])) for c in CLASSES}}
    ref=summary['FLAT_AVG3']; cand=summary['SEMANTIC_STACK']; delta={'meanMacroAuc':cand['meanMacroAuc']-ref['meanMacroAuc'],'worstMacroAuc':cand['worstMacroAuc']-ref['worstMacroAuc'],'macroAp':cand['meanMacroAp']-ref['meanMacroAp'],'logLossImprovement':ref['meanLogLoss']-cand['meanLogLoss'],'balancedAccuracy':cand['meanBalancedAccuracy']-ref['meanBalancedAccuracy'],'handoffRecall':cand['meanRecall']['HANDOFF_ALLOW']-ref['meanRecall']['HANDOFF_ALLOW'],'observeRecall':cand['meanRecall']['OBSERVE_NO_EVENT']-ref['meanRecall']['OBSERVE_NO_EVENT']}
    keep=bool(delta['meanMacroAuc']>0 and delta['worstMacroAuc']>0 and delta['logLossImprovement']>0 and delta['handoffRecall']>=0 and delta['observeRecall']>=0)
    art={'version':'R4_MANAGEMENT_SEMANTIC_STACKING_ARBITER_V1','researchOnly':True,'actionAuthority':False,'design':{'baseOof':'existing strict chronological OOS multiclass EBM/LGBM/Transformer predictions','newBeliefs':['p_transition from dedicated binary EBM/LGBM/Transformer','p_handoff_given_transition from dedicated transition-only EBM/LGBM/Transformer'],'arbiter':'small HistGradientBoostingClassifier','evaluation':'block2 trained only block1 OOS; block3 only blocks1-2; block4 only blocks1-3'},'coverage':{'targetMarkets':int(d.market_id.nunique()),'oofRows':int(len(oof)),'evaluationMarkets':int(sum(e['testMarkets'] for e in evals)),'evaluationRows':int(sum(e['testRows'] for e in evals))},'features':{'baseStack':base_stack_feats,'semanticStack':semantic_stack_feats,'beliefOnlyStack':belief_only_feats},'epochs':epoch_log,'summary':summary,'semanticStackVsFlat':delta,'keepRulePassed':keep,'decision':'KEEP_SEMANTIC_STACK' if keep else 'DO_NOT_PROMOTE_SEMANTIC_STACK_V1','blocks':evals,'oofRows':str(OOF_OUT.relative_to(ROOT)).replace('\\','/'),'guards':['All base and specialist beliefs for an evaluation row are produced by models trained only on earlier Target markets.','Stacking arbiter trains only on prior chronological OOS blocks.','No threshold or feature-weight sweep.','No settlement/winner/future market labels used as runtime features.','Research only; no action authority.']}
    OUT.write_text(json.dumps(art,indent=2),encoding='utf-8'); print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary,'semanticStackVsFlat':delta,'keepRulePassed':keep},indent=2))
if __name__=='__main__': main()
