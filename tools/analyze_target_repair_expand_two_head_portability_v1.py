from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, accuracy_score

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_CROSS_TIMEFRAME_ACTION_VALUE_DECISION_SLICE_V1_20260907.csv'
PREREG=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_REPAIR_EXPAND_TWO_HEAD_PORTABILITY_PREREG_V1_20260909.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_REPAIR_EXPAND_TWO_HEAD_PORTABILITY_RESULT_V1_20260909.json'

SERVICE=[
 'prev_service_fraction','repair_progress_since_last_expand','recent3_repair_share',
 'same_role_streak','same_side_streak','parent_gap_frac','last_repair_age_frac']
OPPORTUNITY=[
 'dominant_mid','dominant_mid_delta_5updates','dominant_mid_delta_since_prev_action',
 'paired_coverage','imbalance_ratio','combined_avg_pair_edge','spread_ticks',
 'dominant_depth_imbalance','floor']
SETS={'SERVICE_ONLY':SERVICE,'OPPORTUNITY_ONLY':OPPORTUNITY,'JOINT':SERVICE+OPPORTUNITY}
EVAL_FRAMES=['BTC5M_HOLDOUT','BTC15M','BTC1H','ETH5M']


def safe_auc(y,p):
    return float(roc_auc_score(y,p)) if len(np.unique(y))==2 else None

def metrics(df, p):
    y=df['y'].to_numpy(dtype=int)
    p=np.asarray(p,float)
    out={
      'n':int(len(df)), 'positive':int(y.sum()), 'positiveShare':float(y.mean()) if len(y) else None,
      'rocAuc':safe_auc(y,p),
      'averagePrecision':float(average_precision_score(y,p)) if len(np.unique(y))==2 else None,
      'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None,
      'accuracyAt05':float(accuracy_score(y,(p>=0.5).astype(int))) if len(y) else None,
    }
    aucs=[]
    for mid,g in df.assign(_p=p).groupby('market_id'):
        yy=g['y'].to_numpy(dtype=int)
        if len(np.unique(yy))==2:
            aucs.append(float(roc_auc_score(yy,g['_p'].to_numpy(float))))
    out['marketEqualAuc']=float(np.mean(aucs)) if aucs else None
    out['marketEqualEligibleMarkets']=int(len(aucs))
    return out

def model():
    return Pipeline([
      ('impute',SimpleImputer(strategy='median')),
      ('scale',StandardScaler()),
      ('lr',LogisticRegression(max_iter=2000, solver='lbfgs')),
    ])

def main():
    prereg=json.loads(PREREG.read_text(encoding='utf-8'))
    df=pd.read_csv(SRC)
    z=df[(df.prev_class=='REPAIR') & (df.current_class.isin(['REPAIR','EXPAND']))].copy()
    z['y']=(z.current_class=='EXPAND').astype(int)

    b=z[z.frame=='BTC5M'].copy()
    first=b.groupby('market_id')['t'].min().sort_values()
    mids=list(first.index)
    cut=max(1, min(len(mids)-1, int(math.floor(len(mids)*0.70))))
    train_mids=set(mids[:cut]); hold_mids=set(mids[cut:])
    train=b[b.market_id.isin(train_mids)].copy()
    evals={
      'BTC5M_HOLDOUT': b[b.market_id.isin(hold_mids)].copy(),
      'BTC15M': z[z.frame=='BTC15M'].copy(),
      'BTC1H': z[z.frame=='BTC1H'].copy(),
      'ETH5M': z[z.frame=='ETH5M'].copy(),
    }

    result={
      'version':'TARGET_REPAIR_EXPAND_TWO_HEAD_PORTABILITY_RESULT_V1_20260909',
      'researchOnly':True,'winnerUsed':False,'runtimeAuthority':False,'BE':0,
      'preregVersion':prereg['version'],
      'split':{
        'btc5mMarketsTotal':len(mids),'btc5mTrainMarkets':len(train_mids),'btc5mHoldoutMarkets':len(hold_mids),
        'trainMarketIds':[int(x) for x in mids[:cut]],'holdoutMarketIds':[int(x) for x in mids[cut:]],
        'trainRows':int(len(train)),'trainPositive':int(train.y.sum())
      },
      'featureSets':SETS,
      'models':{},
      'guards':[
        'current_class used only as label','no current_side/current_route/action_mid/action_price/action_shares features',
        'no winner/settlement','BTC5M chronological-market fit only','zero retrain on BTC5M holdout/BTC15M/BTC1H/ETH5M',
        'no threshold search','existing consumed data only','no HFT/worker/BE/locked cohort'
      ]
    }
    for name,features in SETS.items():
        m=model();m.fit(train[features],train.y)
        rr={}
        for fr,g in evals.items():
            p=m.predict_proba(g[features])[:,1]
            rr[fr]=metrics(g,p)
        result['models'][name]=rr

    joint=result['models']['JOINT'];service=result['models']['SERVICE_ONLY'];opp=result['models']['OPPORTUNITY_ONLY']
    internal=joint['BTC5M_HOLDOUT']['rocAuc'] is not None and joint['BTC5M_HOLDOUT']['rocAuc']>0.55 and joint['BTC5M_HOLDOUT']['rocAuc'] >= max(service['BTC5M_HOLDOUT']['rocAuc'],opp['BTC5M_HOLDOUT']['rocAuc'])-0.01
    ext=['BTC15M','BTC1H','ETH5M']
    increments={fr:joint[fr]['rocAuc']-max(service[fr]['rocAuc'],opp[fr]['rocAuc']) for fr in ext}
    ext_inc=sum(v>=0.015 for v in increments.values())>=2
    ext_noreg=all(v>=-0.01 for v in increments.values())
    me=sum((joint[fr]['marketEqualAuc'] or -1)>0.55 for fr in ext)>=2
    passed=bool(internal and ext_inc and ext_noreg and me)
    result['gate']={
      'internalPass':internal,'externalIncrementsVsBestSingle':increments,
      'externalIncrementPass':ext_inc,'externalNoRegressionPass':ext_noreg,
      'marketEqualPass':me,'allPass':passed,
      'verdict':'PASS_TARGET_ONLY_JOINT_ARBITRATION_SIGNAL' if passed else 'FAIL_JOINT_PORTABLE_INCREMENT'
    }
    result['interpretationBoundary']=[
      'Target-role predictability is descriptive/offline architecture evidence, not OUR causal action value.',
      'A pass would only authorize a separate Target-only live-option incremental test.',
      'A fail stops this two-head representation in current form; no feature-patching loop.',
      'Neither outcome changes locked cohorts or runtime authority.'
    ]
    OUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    compact={'verdict':result['gate']['verdict'],'gate':result['gate'],'metrics':{m:{fr:{k:v for k,v in result['models'][m][fr].items() if k in ('n','rocAuc','marketEqualAuc','averagePrecision','accuracyAt05')} for fr in EVAL_FRAMES} for m in SETS}}
    print(json.dumps(compact,ensure_ascii=False))

if __name__=='__main__': main()
