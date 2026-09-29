from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_information_belief_state_v0_stability.json'
SCORES=ROOT/'data/research/r4_v0/hourly/r4_information_belief_state_v0_scores.csv'
VERSION='R4_INFORMATION_BELIEF_STATE_V0_STABILITY'

# Logic layer: portfolio/phase state, not information-layer beliefs.
LOGIC=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
# Previous raw concatenation baseline retained only as comparison.
RAW_BASE=LOGIC+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
# Semantic belief state V0. No raw spot/futures micro is forwarded.
BELIEF=LOGIC+[
    'belief_direction_confidence',
    'belief_direction_supports_asymmetry',
    'belief_strike_confirmation',
    'belief_placement_readiness_5s',
]

NATIVE_PLACEMENT=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']


def hgb(seed:int):
    return HistGradientBoostingClassifier(
        learning_rate=.055,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,
        l2_regularization=1.0,max_iter=240,random_state=seed,
    )

def metric(y,p):
    return {
        'n':int(len(y)), 'repairRate':float(np.mean(y)),
        'auc':float(roc_auc_score(y,p)),
        'ap':float(average_precision_score(y,p)),
        'logLoss':float(log_loss(y,p,labels=[0,1])),
    }

def chrono_markets(df,tcol):
    return df.groupby('market_id',as_index=False)[tcol].min().sort_values(tcol).market_id.astype(int).tolist()

def safe_scale(train:pd.Series):
    x=train.dropna().astype(float)
    med=float(x.median())
    q1=float(x.quantile(.25));q3=float(x.quantile(.75));iqr=max(1e-9,q3-q1)
    return med,iqr

def main():
    # Frozen semantic placement-readiness expert from strictly earlier cohort.
    p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan)
    p.market_id=p.market_id.astype(int)
    p['predict_edge']=(p.predict_up_mid-.5).abs()
    label='label_next_inferred_placement_any_5s'
    p=p.dropna(subset=['market_id','decision_sampled_at_ms',label]+NATIVE_PLACEMENT).copy()
    p[label]=p[label].astype(int)
    source=hgb(20263001);source.fit(p[NATIVE_PLACEMENT],p[label])

    f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan)
    f.market_id=f.market_id.astype(int);f['repair']=1-f.is_add.astype(int)
    f=f.dropna(subset=['market_id','first_event_ms','repair']+RAW_BASE+NATIVE_PLACEMENT).copy()
    f['belief_placement_readiness_5s']=source.predict_proba(f[NATIVE_PLACEMENT])[:,1]
    f['belief_direction_confidence']=f.predict_edge.astype(float)
    f['belief_direction_supports_asymmetry']=f.predict_supports_dominant.astype(float)*2.0-1.0

    markets=chrono_markets(f,'first_event_ms')
    n=len(markets)
    # Four fixed expanding-window forward blocks. Initial history = first 55 markets; no random split.
    initial=min(55,max(1,n-4))
    remaining=n-initial
    sizes=[remaining//4]*4
    for i in range(remaining%4): sizes[i]+=1
    blocks=[];cur=initial
    for bi,sz in enumerate(sizes,1):
        if sz<=0: continue
        train_ms=markets[:cur];test_ms=markets[cur:cur+sz];cur+=sz
        blocks.append((bi,train_ms,test_ms))

    all_rows=[];block_results=[]
    for bi,train_ms,test_ms in blocks:
        tr=f[f.market_id.isin(set(train_ms))].copy();te=f[f.market_id.isin(set(test_ms))].copy()
        # Robust strike normalization fitted only on each block's historical training rows, no action labels.
        med,iqr=safe_scale(tr.strike_toward_dominant_bps)
        tr['belief_strike_confirmation']=np.clip((tr.strike_toward_dominant_bps-med)/iqr,-4,4)
        te['belief_strike_confirmation']=np.clip((te.strike_toward_dominant_bps-med)/iqr,-4,4)
        models={}
        block={'block':bi,'trainMarkets':len(train_ms),'testMarkets':test_ms,'strikeScale':{'median':med,'iqr':iqr},'metrics':{}}
        for j,(name,feats) in enumerate([('LOGIC_ONLY',LOGIC),('RAW_BASE',RAW_BASE),('BELIEF_STATE_V0',BELIEF)]):
            m=hgb(20263100+bi*10+j);m.fit(tr[feats],tr.repair);models[name]=m
            pr=m.predict_proba(te[feats])[:,1]
            block['metrics'][name]=metric(te.repair.to_numpy(),pr)
            te[f'pred_{name}']=pr
        base=block['metrics']['RAW_BASE'];alt=block['metrics']['BELIEF_STATE_V0']
        block['deltaBeliefVsRaw']={
            'auc':alt['auc']-base['auc'],'ap':alt['ap']-base['ap'],
            'logLossImprovement':base['logLoss']-alt['logLoss'],
        }
        # Observed, model-free Target behavior consistency by confidence regime and strike support.
        obs=[]
        for cname,mask in [
            ('LOW_LT_.10',te.predict_edge<.10),
            ('MOD_.10_.30',(te.predict_edge>=.10)&(te.predict_edge<.30)),
            ('EXT_GE_.30',te.predict_edge>=.30),
        ]:
            z=te[mask]
            for sup in (0,1):
                q=z[z.spot_supports_dominant.astype(int)==sup]
                if len(q)>=20:
                    obs.append({'confidence':cname,'support':sup,'n':int(len(q)),'repairRate':float(q.repair.mean())})
        block['observedModeByConfidenceStrike']=obs
        block_results.append(block)
        keep=['market_id','first_event_ms','repair','predict_edge','spot_supports_dominant','pre_abs_payoff_gap','belief_direction_confidence','belief_direction_supports_asymmetry','belief_strike_confirmation','belief_placement_readiness_5s','pred_LOGIC_ONLY','pred_RAW_BASE','pred_BELIEF_STATE_V0']
        x=te[keep].copy();x['block']=bi;all_rows.append(x)

    def summarize(name):
        vals=[b['metrics'][name] for b in block_results]
        return {
            'blocks':len(vals),
            'meanAuc':float(np.mean([x['auc'] for x in vals])),
            'worstAuc':float(np.min([x['auc'] for x in vals])),
            'stdAuc':float(np.std([x['auc'] for x in vals],ddof=0)),
            'meanAp':float(np.mean([x['ap'] for x in vals])),
            'worstAp':float(np.min([x['ap'] for x in vals])),
            'meanLogLoss':float(np.mean([x['logLoss'] for x in vals])),
            'worstLogLoss':float(np.max([x['logLoss'] for x in vals])),
        }
    summaries={k:summarize(k) for k in ['LOGIC_ONLY','RAW_BASE','BELIEF_STATE_V0']}
    wins={
        'aucBlocksBeliefBetterOrEqual':sum(b['metrics']['BELIEF_STATE_V0']['auc']>=b['metrics']['RAW_BASE']['auc'] for b in block_results),
        'apBlocksBeliefBetterOrEqual':sum(b['metrics']['BELIEF_STATE_V0']['ap']>=b['metrics']['RAW_BASE']['ap'] for b in block_results),
        'logLossBlocksBeliefBetterOrEqual':sum(b['metrics']['BELIEF_STATE_V0']['logLoss']<=b['metrics']['RAW_BASE']['logLoss'] for b in block_results),
        'allThreeBetterBlocks':sum(
            b['metrics']['BELIEF_STATE_V0']['auc']>=b['metrics']['RAW_BASE']['auc'] and
            b['metrics']['BELIEF_STATE_V0']['ap']>=b['metrics']['RAW_BASE']['ap'] and
            b['metrics']['BELIEF_STATE_V0']['logLoss']<=b['metrics']['RAW_BASE']['logLoss']
            for b in block_results),
    }
    artifact={
        'version':VERSION,'researchOnly':True,'runtimePromotionAllowed':False,
        'purpose':'Test whether a small semantic information-belief state improves chronological Formation-mode stability rather than merely mean score.',
        'architecture':{
            'informationToBelief':{
                'Predict':'direction confidence/support state',
                'spot-vs-strike':'robust signed strike-confirmation belief',
                'earlier 5s Target placement expert':'frozen semantic placement-readiness belief',
                'raw spot/futures micro':'not forwarded to Formation logic',
            },
            'logicState':LOGIC,
            'beliefState':BELIEF,
        },
        'coverage':{'rows':int(len(f)),'markets':int(f.market_id.nunique()),'sourcePlacementRows':int(len(p)),'sourcePlacementMarkets':int(p.market_id.nunique())},
        'temporalGuard':{'placementSourceMaxMs':int(p.decision_sampled_at_ms.max()),'formationMinMs':int(f.first_event_ms.min()),'strictlyEarlier':bool(p.decision_sampled_at_ms.max()<f.first_event_ms.min())},
        'forwardDesign':{'initialHistoryMarkets':initial,'blocks':len(block_results),'method':'expanding chronological market windows; 4 forward blocks; no random split'},
        'blockResults':block_results,'summary':summaries,'stabilityWins':wins,
        'guards':['No winner/settlement feature.','No raw spot/futures micro forwarded to Formation logic.','Strike normalization fit on historical rows only and does not use action labels.','Placement-readiness expert trained on a completely earlier cohort and frozen.','No threshold or hyperparameter sweep.','Belief state remains teacher/context only; no order authority.']
    }
    OUT.write_text(json.dumps(artifact,ensure_ascii=False,indent=2),encoding='utf-8')
    pd.concat(all_rows,ignore_index=True).to_csv(SCORES,index=False)
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':artifact['coverage'],'temporalGuard':artifact['temporalGuard'],'summary':summaries,'stabilityWins':wins,'blockResults':block_results},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
