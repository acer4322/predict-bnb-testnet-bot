from __future__ import annotations

import bisect
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score, classification_report, confusion_matrix, f1_score, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'data' / 'research' / 'target_maker_taker_coordination_big_v1'
OUT = ROOT / 'data' / 'research' / 'supervisor_options_v0'
GENERAL = SRC / 'target_general_maker_side_hazard_v1.csv'
TAKER_EVENTS = SRC / 'taker_event_states_v1.csv'
REPORT = OUT / 'supervisor_mode_v0_report.json'
ART = OUT / 'supervisor_mode_v0_hgb.joblib'
STATES = OUT / 'supervisor_mode_v0_states.csv'
SEED = 20260820
TAKER_HORIZON_MS = 3000

CURRENT = [
    'seconds_left',
    'maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage',
    'taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage',
    'combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage',
    'worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign',
    'last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms',
    'last_taker_up_age_ms','last_taker_down_age_ms',
    'maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s',
    'maker_shares_10s','taker_shares_10s','maker_absnet_change_10s','combined_absnet_change_10s',
    'maker_avg_pair_edge','taker_avg_pair_edge','combined_avg_pair_edge',
    'up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth',
    'down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth',
    'pair_bid_edge','pair_ask_edge','dominant_opp_bid_pair_edge',
    'last_place_age_ms','placements_1s','placements_5s','placements_10s','placement_side_balance_5s','placement_side_balance_10s',
]

MEM_BASE = [
    'maker_net','maker_abs_net','maker_paired_coverage',
    'combined_net','combined_abs_net','combined_paired_coverage',
    'worst_case_floor','best_case_pnl','maker_avg_pair_edge','combined_avg_pair_edge',
    'pair_bid_edge','pair_ask_edge','placements_5s','taker_fills_5s',
]
LAGS = (3,10,30)


def numeric(frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    return frame[cols].apply(pd.to_numeric, errors='coerce')


def build() -> tuple[pd.DataFrame, list[str]]:
    d = pd.read_csv(GENERAL).sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True)
    d = d[pd.to_numeric(d.seconds_left,errors='coerce').between(3.0,297.0,inclusive='both')].copy()
    d = d[d.groupby('market_id',sort=False).cumcount().mod(2).eq(0)].copy().reset_index(drop=True)

    te = pd.read_csv(TAKER_EVENTS,usecols=['market_id','checkpoint_ms'])
    future = {}
    for mid,x in te.groupby('market_id',sort=False):
        future[int(mid)] = sorted(pd.to_numeric(x.checkpoint_ms,errors='coerce').dropna().astype('int64').tolist())
    taker_next3 = np.zeros(len(d),dtype=np.int8)
    taker_delay = np.full(len(d),np.nan)
    for i,r in enumerate(d[['market_id','checkpoint_ms']].itertuples(index=False)):
        ts=future.get(int(r.market_id),[]); t=int(r.checkpoint_ms); j=bisect.bisect_right(ts,t)
        if j<len(ts):
            delay=ts[j]-t; taker_delay[i]=delay
            if 0<delay<=TAKER_HORIZON_MS:taker_next3[i]=1
    maker_next1 = (
        pd.to_numeric(d.label_up_next1s,errors='coerce').fillna(0).astype(int).gt(0)
        | pd.to_numeric(d.label_down_next1s,errors='coerce').fillna(0).astype(int).gt(0)
    ).to_numpy()
    mode=np.full(len(d),'HOLD',dtype=object); mode[maker_next1]='MAKER'; mode[taker_next3.astype(bool)]='TAKER'
    d['option_mode']=mode; d['taker_next3s']=taker_next3; d['next_taker_delay_ms']=taker_delay

    # Vectorized same-market strict-past deltas. No future state enters features.
    gb=d.groupby('market_id',sort=False)
    mem={}
    for base in MEM_BASE:
        cur=pd.to_numeric(d[base],errors='coerce')
        for lag in LAGS:
            past=pd.to_numeric(gb[base].shift(lag),errors='coerce')
            mem[f'{base}_delta{lag}s']=cur-past
    mem_df=pd.DataFrame(mem,index=d.index)
    d=pd.concat([d,mem_df],axis=1)
    return d,list(mem_df.columns)


def sample_weights(y: pd.Series) -> np.ndarray:
    counts=y.value_counts(); n=len(y)
    mp={k:float(np.sqrt(n/max(1,int(v)))) for k,v in counts.items()}
    w=y.map(mp).astype(float).to_numpy(); return w/w.mean()


def fit(train: pd.DataFrame, features: list[str]) -> HistGradientBoostingClassifier:
    m=HistGradientBoostingClassifier(
        learning_rate=.08,max_iter=50,max_leaf_nodes=15,min_samples_leaf=120,
        l2_regularization=1.0,early_stopping=True,validation_fraction=.1,n_iter_no_change=15,
        random_state=SEED,
    )
    m.fit(numeric(train,features),train.option_mode.astype(str),sample_weight=sample_weights(train.option_mode.astype(str)))
    return m


def metrics(model, x: pd.DataFrame, features: list[str]) -> dict:
    y=x.option_mode.astype(str); xx=numeric(x,features); pred=model.predict(xx); p=model.predict_proba(xx); labels=list(model.classes_)
    y_idx=pd.Categorical(y,categories=labels).codes
    macro_auc=None
    try: macro_auc=float(roc_auc_score(y,p,labels=labels,multi_class='ovr',average='macro'))
    except Exception: pass
    rep=classification_report(y,pred,labels=labels,output_dict=True,zero_division=0)
    return {
        'n':int(len(y)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'macroF1':float(f1_score(y,pred,average='macro')),
        'macroOvrAuc':macro_auc,'logLoss':float(log_loss(y,p,labels=labels)),'labels':[str(z) for z in labels],
        'confusionMatrix':confusion_matrix(y,pred,labels=labels).astype(int).tolist(),
        'perClass':{k:{q:float(rep[k][q]) for q in ('precision','recall','f1-score')} for k in labels},
    }


def main() -> None:
    OUT.mkdir(parents=True,exist_ok=True)
    d,mem=build(); print(json.dumps({'progress':'BUILT','rows':len(d),'markets':int(d.market_id.nunique())}),flush=True); features={'currentOnly':CURRENT,'currentPlusMemory':CURRENT+mem}
    markets=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id'])
    ids=markets.market_id.astype(int).tolist(); a=int(len(ids)*.70); b=int(len(ids)*.85)
    split_ids={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])}
    parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in split_ids.items()}

    models={}; results={}
    for name,feats in features.items():
        models[name]=fit(parts['train'],feats)
        print(json.dumps({'progress':'FIT','variant':name}),flush=True)
        results[name]={k:metrics(models[name],x,feats) for k,x in parts.items()}

    artifact={
        'version':'SUPERVISOR_MODE_V0_HGB_RESEARCH','researchOnly':True,'runtimePromotion':False,
        'model':models['currentPlusMemory'],'features':features['currentPlusMemory'],'classes':list(models['currentPlusMemory'].classes_),
        'label':'TAKER if Target Taker starts within3s; else MAKER if Target Maker placement label within1s; else HOLD',
        'memory':'same-market strict-past deltas at 3/10/30 checkpoints','trainingMarkets':sorted(split_ids['train']),
        'guards':['No winner/PnL.','No 2026-08-16 special period.','No Target future state as feature.','Research shadow only.'],
    }
    joblib.dump(artifact,ART)

    d[['market_id','market_end_ms','checkpoint_ms','seconds_left','option_mode','taker_next3s','next_taker_delay_ms']+CURRENT+mem].to_csv(STATES,index=False)
    lift={}
    for s in parts:
        lift[s]={k:results['currentPlusMemory'][s][k]-results['currentOnly'][s][k] for k in ('balancedAccuracy','macroF1','macroOvrAuc','logLoss') if results['currentPlusMemory'][s][k] is not None and results['currentOnly'][s][k] is not None}
    report={
        'reportVersion':'SUPERVISOR_MODE_V0','researchOnly':True,
        'question':'Can ordinary-market strict-past state predict Target high-level HOLD/MAKER/TAKER mode, and does trajectory memory add stable chronological lift?',
        'source':{'rows':int(len(d)),'markets':int(d.market_id.nunique()),'minEndMs':int(d.market_end_ms.min()),'maxEndMs':int(d.market_end_ms.max()),'special20260816':'ABSENT'},
        'labels':{'priority':'TAKER > MAKER > HOLD','takerHorizonMs':TAKER_HORIZON_MS,'modeCounts':d.option_mode.value_counts().to_dict(),'modeRates':d.option_mode.value_counts(normalize=True).to_dict()},
        'splitMarkets':{k:len(v) for k,v in split_ids.items()},'chronology':{k:{'minEndMs':int(x.market_end_ms.min()),'maxEndMs':int(x.market_end_ms.max())} for k,x in parts.items()},
        'features':{'current':len(CURRENT),'memoryDeltas':len(mem),'memoryBases':MEM_BASE,'lagsSeconds':list(LAGS)},
        'results':results,'memoryLift':lift,'artifact':str(ART),'statesFile':str(STATES),
        'interpretationRule':'Balanced accuracy 1/3 is trivial-class baseline. Do not deploy from this report; if validation/test are stable, next train conditional Maker and Taker option gates, then evaluate the frozen driver on OUR-state as shadow advisor.',
        'guards':['No winner/PnL teacher or feature.','Level-1 ordinary markets only.','No threshold/PnL sweep.','8784/8786 untouched.','No Echtgeld changes.'],
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
