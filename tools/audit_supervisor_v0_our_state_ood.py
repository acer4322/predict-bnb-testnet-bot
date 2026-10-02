from __future__ import annotations
import json, math, glob
from pathlib import Path
import joblib, numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
OUT=ROOT/'data'/'research'/'supervisor_options_v0'
MODE_ART=OUT/'supervisor_mode_v0_currentPlusMemory.joblib'
COND_ART=OUT/'supervisor_conditional_gates_v0.joblib'
REPORT=OUT/'supervisor_v0_our_state_ood_audit_v0.json'
ROWS=OUT/'supervisor_v0_our_state_predictions_v0.csv'

CURRENT=[
    'seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage',
    'taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage',
    'combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage',
    'worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign',
    'last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms',
    'maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s',
    'maker_shares_10s','taker_shares_10s','maker_absnet_change_10s','combined_absnet_change_10s',
    'maker_avg_pair_edge','taker_avg_pair_edge','combined_avg_pair_edge',
    'up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth',
    'pair_bid_edge','pair_ask_edge','dominant_opp_bid_pair_edge','last_place_age_ms','placements_1s','placements_5s','placements_10s','placement_side_balance_5s','placement_side_balance_10s']
MEM_BASE=['maker_net','maker_abs_net','maker_paired_coverage','combined_net','combined_abs_net','combined_paired_coverage','worst_case_floor','best_case_pnl','maker_avg_pair_edge','combined_avg_pair_edge','pair_bid_edge','pair_ask_edge','placements_5s','taker_fills_5s']
LAGS=(3,10,30)  # after ~2s sampling: about 6/20/60 seconds

def num(df,cols): return df[cols].apply(pd.to_numeric,errors='coerce')

def add_memory(d, market_col):
    d=d.copy(); gb=d.groupby(market_col,sort=False); mem={}
    for base in MEM_BASE:
        cur=pd.to_numeric(d[base],errors='coerce')
        for lag in LAGS:
            past=pd.to_numeric(gb[base].shift(lag),errors='coerce')
            mem[f'{base}_delta{lag}s']=cur-past
    return pd.concat([d,pd.DataFrame(mem,index=d.index)],axis=1)

def sample_our_2s(d):
    out=[]
    for _,g in d.groupby('market_id',sort=False):
        g=g.sort_values('checkpoint_ms')
        keep=[]; last=-10**18
        for i,t in zip(g.index,pd.to_numeric(g.checkpoint_ms,errors='coerce').fillna(0).astype('int64')):
            if t-last>=1800:
                keep.append(i); last=t
        out.append(g.loc[keep])
    return pd.concat(out,ignore_index=True) if out else d.iloc[0:0].copy()

def load_our():
    fs=sorted(glob.glob(str(SRC/'target_blind_promoted_controller_closed_loop_v7_residual_on_dev*_states.csv')))
    frames=[]
    for f in fs:
        x=pd.read_csv(f)
        x=x.rename(columns={'marketId':'market_id','windowEndMs':'market_end_ms','atMs':'checkpoint_ms'})
        x['source_file']=Path(f).name
        frames.append(x)
    d=pd.concat(frames,ignore_index=True)
    d=d[pd.to_numeric(d.seconds_left,errors='coerce').between(3.0,297.0,inclusive='both')].copy()
    d=sample_our_2s(d)
    d=add_memory(d,'market_id')
    return d,fs

def load_target_reference(training_ids):
    d=pd.read_csv(SRC/'target_general_maker_side_hazard_v1.csv').sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True)
    d=d[pd.to_numeric(d.seconds_left,errors='coerce').between(3.0,297.0,inclusive='both')].copy()
    d=d[d.groupby('market_id',sort=False).cumcount().mod(2).eq(0)].copy().reset_index(drop=True)
    d=add_memory(d,'market_id')
    train=d[d.market_id.astype(int).isin(set(map(int,training_ids)))].copy()
    rest=d[~d.market_id.astype(int).isin(set(map(int,training_ids)))].copy()
    return train,rest

def feature_shift(train,our,features):
    rows=[]
    for f in features:
        a=pd.to_numeric(train[f],errors='coerce'); b=pd.to_numeric(our[f],errors='coerce')
        av=a.dropna(); bv=b.dropna()
        miss_a=float(a.isna().mean()); miss_b=float(b.isna().mean())
        if len(av)>=20 and len(bv)>=5:
            p01,p25,p50,p75,p99=np.nanquantile(av,[.01,.25,.5,.75,.99]); med_b=float(np.nanmedian(bv)); iqr=max(float(p75-p25),1e-9)
            outside=float(((bv<p01)|(bv>p99)).mean()); med_shift=abs(med_b-float(p50))/iqr
        else:
            p01=p99=p50=med_b=outside=med_shift=math.nan
        rows.append({'feature':f,'targetMissing':miss_a,'ourMissing':miss_b,'missingDelta':miss_b-miss_a,'ourOutsideTargetP01P99':outside,'medianShiftIqr':med_shift,'targetMedian':p50,'ourMedian':med_b})
    return pd.DataFrame(rows)

def prob_summary(model,frame,features,prefix):
    X=num(frame,features); p=model.predict_proba(X); cls=list(map(str,model.classes_)); pred=np.asarray(cls,dtype=object)[np.argmax(p,axis=1)]
    mx=p.max(axis=1); ent=-(np.clip(p,1e-12,1)*np.log(np.clip(p,1e-12,1))).sum(axis=1)/math.log(len(cls))
    out={'n':len(frame),'predictedRates':{c:float(np.mean(pred==c)) for c in cls},'meanMaxProbability':float(mx.mean()),'p50MaxProbability':float(np.quantile(mx,.5)),'p90MaxProbability':float(np.quantile(mx,.9)),'saturationGt090':float(np.mean(mx>.90)),'normalizedEntropyMean':float(ent.mean())}
    for j,c in enumerate(cls): out[f'meanP_{c}']=float(p[:,j].mean())
    return out,p,pred

def dwell_stats(frame,pred):
    tmp=frame[['market_id','checkpoint_ms']].copy(); tmp['pred']=pred; dw=[]; trans=[]
    for _,g in tmp.groupby('market_id',sort=False):
        g=g.sort_values('checkpoint_ms'); vals=g.pred.tolist(); ts=g.checkpoint_ms.astype('int64').tolist()
        if not vals: continue
        ntr=sum(vals[i]!=vals[i-1] for i in range(1,len(vals))); trans.append(ntr)
        st=0
        for i in range(1,len(vals)+1):
            if i==len(vals) or vals[i]!=vals[st]:
                dur=max(2.0,(ts[i-1]-ts[st])/1000.0+2.0); dw.append((vals[st],dur)); st=i
    by={}
    for c in sorted(set(x[0] for x in dw)):
        z=[x[1] for x in dw if x[0]==c]; by[c]={'episodes':len(z),'medianSeconds':float(np.median(z)),'p90Seconds':float(np.quantile(z,.9))}
    return {'transitionsPerMarketMean':float(np.mean(trans)) if trans else None,'transitionsPerMarketMedian':float(np.median(trans)) if trans else None,'dwellByMode':by}

def auc_safe(y,p):
    y=np.asarray(y,dtype=int)
    return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None

def main():
    mode_art=joblib.load(MODE_ART); cond_art=joblib.load(COND_ART); features=list(mode_art['features'])
    our,files=load_our(); train,target_rest=load_target_reference(mode_art['trainingMarkets'])
    shifts=feature_shift(train,our,features)
    target_summary,tp,t_pred=prob_summary(mode_art['model'],target_rest,features,'target')
    our_summary,op,o_pred=prob_summary(mode_art['model'],our,features,'our')
    cls=list(map(str,mode_art['model'].classes_)); ci={c:i for i,c in enumerate(cls)}
    cm=cond_art['models']; cp_m=cm['maker'].predict_proba(num(our,cm['features']))[:,list(map(str,cm['maker'].classes_)).index('PASSIVE_REPAIR')]
    cp_t=cm['taker'].predict_proba(num(our,cm['features']))[:,list(map(str,cm['taker'].classes_)).index('ACTIVE_REPAIR')]
    episode=pd.to_numeric(our.get('episodeActive',0),errors='coerce').fillna(0).astype(int).to_numpy()
    ready=pd.to_numeric(our.get('readiness',0),errors='coerce').fillna(0).astype(int).to_numpy()
    coherence={
      'makerRepairProb':{'meanEpisode':float(cp_m[episode==1].mean()) if np.any(episode==1) else None,'meanNoEpisode':float(cp_m[episode==0].mean()) if np.any(episode==0) else None,'aucVsOurEpisodeActive':auc_safe(episode,cp_m)},
      'takerRepairProb':{'meanEpisode':float(cp_t[episode==1].mean()) if np.any(episode==1) else None,'meanNoEpisode':float(cp_t[episode==0].mean()) if np.any(episode==0) else None,'aucVsOurEpisodeActive':auc_safe(episode,cp_t)},
      'modeTakerProb':{'meanReady':float(op[ready==1,ci['TAKER']].mean()) if np.any(ready==1) else None,'meanNotReady':float(op[ready==0,ci['TAKER']].mean()) if np.any(ready==0) else None,'aucVsOurReadiness':auc_safe(ready,op[:,ci['TAKER']])},
      'modeMakerProb':{'meanEpisode':float(op[episode==1,ci['MAKER']].mean()) if np.any(episode==1) else None,'meanNoEpisode':float(op[episode==0,ci['MAKER']].mean()) if np.any(episode==0) else None},
    }
    outcols=['market_id','market_end_ms','checkpoint_ms','seconds_left','episodeActive','episodeKind','readiness']
    pred=our[outcols].copy()
    for j,c in enumerate(cls): pred[f'pMode_{c}']=op[:,j]
    pred['predMode']=o_pred; pred['pPassiveRepairOption']=cp_m; pred['pActiveRepairOption']=cp_t
    pred.to_csv(ROWS,index=False)
    severe=shifts[(shifts.medianShiftIqr>2.0)|(shifts.ourOutsideTargetP01P99>0.25)|(shifts.missingDelta.abs()>0.25)].sort_values(['medianShiftIqr','ourOutsideTargetP01P99'],ascending=False)
    rep={'reportVersion':'SUPERVISOR_V0_OUR_STATE_OOD_AUDIT_V0','researchOnly':True,'runtimePromotion':False,
      'ourSource':{'files':[Path(x).name for x in files],'markets':int(our.market_id.nunique()),'rows2s':len(our),'cohort':'EXPOSED_DEV00_24_ONLY; final75_99 not used'},
      'targetReference':{'trainRows':len(train),'otherRows':len(target_rest),'trainingMarkets':len(set(mode_art['trainingMarkets']))},
      'memorySemanticsCorrection':'Training first downsamples to ~2s cadence then shift(3,10,30), therefore memory corresponds to about 6/20/60 seconds, not 3/10/30 seconds.',
      'featureShift':{'features':len(features),'medianOutsideP01P99':float(shifts.ourOutsideTargetP01P99.median()),'meanOutsideP01P99':float(shifts.ourOutsideTargetP01P99.mean()),'featuresOutsideGt25pct':int((shifts.ourOutsideTargetP01P99>0.25).sum()),'featuresMedianShiftGt2Iqr':int((shifts.medianShiftIqr>2).sum()),'featuresMissingDeltaGt25pp':int((shifts.missingDelta.abs()>0.25).sum()),'severeFeatures':severe.head(20).replace({np.nan:None}).to_dict('records')},
      'modelBehavior':{'targetNonTrainReference':target_summary,'ourState':our_summary,'ourDwell':dwell_stats(our,o_pred)},
      'semanticCoherenceAgainstExistingR2State':coherence,
      'decisionRule':'Pass only if OUR-state is not broadly OOD, mode probabilities are not saturated/collapsed, and repair-option probabilities remain directionally coherent with OUR episode/readiness state. No PnL used.',
      'predictionsFile':str(ROWS),'guards':['No winner/PnL.','No special 2026-08-16 markets.','No final75-99 cohort.','No 8784/8786 changes.','No Echtgeld changes.']}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
