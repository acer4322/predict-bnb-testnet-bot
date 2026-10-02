from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score
from sklearn.tree import DecisionTreeClassifier, export_text

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_target_information_belief_stack_v1.json'
VERSION='R4_TARGET_INFORMATION_BELIEF_STACK_V1'

LOGIC=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
PREDICT_STRIKE=['seconds_left','predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
SPOT=['seconds_left','spot_queue_imbalance','spot_taker_imbalance_250ms','spot_taker_imbalance_1s','spot_return_250ms_bps','spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps','spot_micro_minus_spot_bps']
FUT=['seconds_left','futures_queue_imbalance','futures_taker_imbalance_250ms','futures_taker_imbalance_1s','futures_return_250ms_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps','futures_micro_minus_futures_bps']
BASIS=['seconds_left','perp_spot_basis_bps','spot_minus_chainlink_bps','chainlink_minus_strike_bps']
ALL_RAW=sorted(set(LOGIC+PREDICT_STRIKE+SPOT+FUT+BASIS), key=lambda x:(LOGIC+PREDICT_STRIKE+SPOT+FUT+BASIS).index(x))
EXPERTS={'belief_predict_strike':PREDICT_STRIKE,'belief_spot_micro':SPOT,'belief_futures_micro':FUT,'belief_basis_reference':BASIS}


def hgb(seed=20260827):
    return HistGradientBoostingClassifier(learning_rate=.055,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1.0,max_iter=240,random_state=seed)

def metric(y,p):
    return {'n':int(len(y)),'repairRate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def market_split(df):
    mt=df.groupby('market_id',as_index=False).first_event_ms.min().sort_values('first_event_ms')
    ms=mt.market_id.astype(int).tolist(); n=len(ms); a=max(1,int(n*.70)); b=min(n,max(a+1,int(n*.85)))
    return {'train':ms[:a],'validation':ms[a:b],'test':ms[b:]}

def expanding_oof(train, train_markets, feats, seed):
    # strict chronological OOF: first 30 markets are warmup only; four later blocks are predicted by prior markets.
    warm=min(30,max(12,int(len(train_markets)*.40)))
    rest=train_markets[warm:]
    blocks=np.array_split(np.array(rest,dtype=int),4)
    out=pd.Series(index=train.index,dtype=float)
    fold_meta=[]
    seen=train_markets[:warm]
    for i,blk in enumerate(blocks):
        block=[int(x) for x in blk.tolist()]
        if not block: continue
        tr=train[train.market_id.isin(set(seen))]
        va=train[train.market_id.isin(set(block))]
        if tr.repair.nunique()<2 or va.empty: continue
        m=hgb(seed+i); m.fit(tr[feats],tr.repair)
        out.loc[va.index]=m.predict_proba(va[feats])[:,1]
        fold_meta.append({'fold':i,'trainMarkets':len(set(seen)),'scoreMarkets':len(block),'rows':int(len(va)),'firstScoreMarket':block[0],'lastScoreMarket':block[-1]})
        seen=seen+block
    return out,fold_meta

def main():
    df=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan)
    df['market_id']=df.market_id.astype(int); df['repair']=1-df.is_add.astype(int)
    split=market_split(df); parts={k:df[df.market_id.isin(set(v))].copy() for k,v in split.items()}
    train=parts['train']

    expert_models={}; expert_metrics={}; oof_cols=[]; fold_info={}
    for j,(name,feats) in enumerate(EXPERTS.items()):
        oof,meta=expanding_oof(train,split['train'],feats,20260910+j*10)
        col=name+'_score'; train[col]=oof; oof_cols.append(col); fold_info[name]=meta
        m=hgb(20261010+j); m.fit(train[feats],train.repair); expert_models[name]=m
        expert_metrics[name]={}
        for sk in ('validation','test'):
            p=parts[sk]; pred=m.predict_proba(p[feats])[:,1]
            parts[sk][col]=pred; expert_metrics[name][sk]=metric(p.repair.to_numpy(),pred)

    meta_train=train.dropna(subset=oof_cols).copy()
    meta_feats_logic=LOGIC+oof_cols
    meta_feats_scores=oof_cols
    meta_models={}; results={}
    for name,feats in [('EXPERTS_ONLY',meta_feats_scores),('LOGIC_PLUS_BELIEF_EXPERTS',meta_feats_logic)]:
        m=hgb(20261100+len(feats)); m.fit(meta_train[feats],meta_train.repair); meta_models[name]=m; results[name]={}
        results[name]['trainOOF']=metric(meta_train.repair.to_numpy(),m.predict_proba(meta_train[feats])[:,1])
        for sk in ('validation','test'):
            p=parts[sk]; results[name][sk]=metric(p.repair.to_numpy(),m.predict_proba(p[feats])[:,1])

    # Raw baselines trained on the full designated train period, for exact same validation/test.
    baselines={}
    for name,feats in [('LOGIC_ONLY',LOGIC),('LOGIC_PREDICT_STRIKE',sorted(set(LOGIC+PREDICT_STRIKE), key=lambda x:(LOGIC+PREDICT_STRIKE).index(x))),('ALL_RAW_PUBLIC',ALL_RAW)]:
        m=hgb(20261200+len(feats)); m.fit(train[feats],train.repair); baselines[name]={}
        for sk in ('validation','test'):
            p=parts[sk]; baselines[name][sk]=metric(p.repair.to_numpy(),m.predict_proba(p[feats])[:,1])

    # Simple interpretable logic tree over belief scores + portfolio geometry, trained only on strict OOF rows.
    tree=DecisionTreeClassifier(max_depth=3,min_samples_leaf=100,class_weight='balanced',random_state=20260827)
    tree.fit(meta_train[meta_feats_logic],meta_train.repair)
    tree_eval={}
    for sk in ('validation','test'):
        p=parts[sk]; tree_eval[sk]=metric(p.repair.to_numpy(),tree.predict_proba(p[meta_feats_logic])[:,1])
    imps=sorted([{'feature':f,'importance':float(v)} for f,v in zip(meta_feats_logic,tree.feature_importances_)],key=lambda x:x['importance'],reverse=True)

    # Agreement/disagreement diagnostics on untouched test.
    test=parts['test'].copy(); diag=[]
    ps='belief_predict_strike_score'; sm='belief_spot_micro_score'; fm='belief_futures_micro_score'; br='belief_basis_reference_score'
    test['public_micro_mean']=(test[sm]+test[fm])/2
    test['belief_disagreement']=abs(test[ps]-test.public_micro_mean)
    for label,mask in [
        ('PS_HIGH_MICRO_LOW',(test[ps]>=.55)&(test.public_micro_mean<.45)),
        ('PS_LOW_MICRO_HIGH',(test[ps]<.45)&(test.public_micro_mean>=.55)),
        ('AGREE_HIGH',(test[ps]>=.55)&(test.public_micro_mean>=.55)),
        ('AGREE_LOW',(test[ps]<.45)&(test.public_micro_mean<.45)),
    ]:
        z=test[mask]
        if len(z)>=20: diag.append({'state':label,'n':int(len(z)),'repairRate':float(z.repair.mean()),'meanPredictStrikeBelief':float(z[ps].mean()),'meanMicroBelief':float(z.public_micro_mean.mean()),'meanGap':float(z.pre_abs_payoff_gap.mean())})

    artifact={
        'version':VERSION,'researchOnly':True,'runtimePromotionAllowed':False,
        'question':'Does a layered Information -> belief experts -> logic architecture generalize better than concatenating raw public signals into one Formation rule model?',
        'coverage':{'rows':int(len(df)),'markets':int(df.market_id.nunique()),'metaTrainOOFRows':int(len(meta_train)),'metaTrainOOFMarkets':int(meta_train.market_id.nunique())},
        'split':{'method':'outer chronological market 70/15/15; expert meta-training uses expanding strict-chronology OOF within outer train','trainMarkets':split['train'],'validationMarkets':split['validation'],'testMarkets':split['test']},
        'experts':EXPERTS,'expertMetrics':expert_metrics,'expertOOFFolds':fold_info,'metaResults':results,'rawBaselines':baselines,
        'shallowLogicTree':{'features':meta_feats_logic,'metrics':tree_eval,'featureImportances':imps,'rules':export_text(tree,feature_names=meta_feats_logic,decimals=4)},
        'testBeliefAgreementAudit':diag,
        'guards':['Expert scores are diagnostic belief proxies trained on Target Formation-mode labels; they are not independently identified latent variables.','No random row split, winner, settlement, threshold sweep, or direct action authority.','Meta logic sees strict chronological OOF expert scores during training to avoid same-row expert leakage.','This experiment tests architecture, not runtime promotion.']
    }
    OUT.write_text(json.dumps(artifact,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':artifact['coverage'],'expertMetrics':expert_metrics,'metaResults':results,'rawBaselines':baselines,'tree':artifact['shallowLogicTree'],'agreement':diag},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
