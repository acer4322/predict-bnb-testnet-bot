from __future__ import annotations
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
CSV=OUT/'forward_handoff_states_v1.csv'
CONTRACT=OUT/'forward_contract_v1.json'
REPORT=OUT/'forward_handoff_error_clusters_v0.json'

def q(x):
    s=pd.to_numeric(x,errors='coerce').dropna()
    if not len(s): return None
    return {'n':int(len(s)),'mean':float(s.mean()),'median':float(s.median()),'p25':float(s.quantile(.25)),'p75':float(s.quantile(.75))}

def main():
    c=json.loads(CONTRACT.read_text(encoding='utf-8'))
    df=pd.read_csv(CSV)
    art=joblib.load(c['artifacts']['handoff']); model=art['model']; fs=art['features']
    X=df[fs].apply(pd.to_numeric,errors='coerce')
    pred=np.asarray(model.predict(X),dtype=str); truth=df.label_handoff.astype(str).to_numpy()
    df=df.copy(); df['pred']=pred; df['correct']=pred==truth
    labels=['BOTH','OPP','PAUSE','SAME']
    cm=confusion_matrix(truth,pred,labels=labels)
    recall={lab:(float(cm[i,i]/cm[i].sum()) if cm[i].sum() else None) for i,lab in enumerate(labels)}
    mask_miss=(truth!='SAME')&(pred=='SAME')
    mask_correct_non=(truth!='SAME')&(pred==truth)
    mask_true_same=(truth=='SAME')
    features=[
      'seconds_left','book_age_ms','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage',
      'taker_abs_net','taker_imbalance_ratio','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage',
      'last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms',
      'maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s',
      'maker_side_streak','taker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s',
      'maker_avg_pair_edge','taker_avg_pair_edge','combined_avg_pair_edge','pair_bid_edge','pair_ask_edge',
      'dominant_opp_bid_pair_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask',
      'up_bid_depth','down_bid_depth','up_top3_bid_depth','down_top3_bid_depth',
      'intervention_shares','intervention_avg_price','post_taker_combined_abs_net',
      'post_taker_combined_imbalance_ratio','post_taker_combined_paired_coverage','post_taker_worst_case_floor'
    ]
    groups={'NON_SAME_MISSED_AS_SAME':mask_miss,'NON_SAME_CORRECT':mask_correct_non,'TRUE_SAME_ALL':mask_true_same}
    summaries={g:{f:q(df.loc[m,f]) for f in features if f in df.columns} for g,m in groups.items()}
    # rank simple standardized median gaps missed-vs-correct using pooled IQR, descriptive only
    ranked=[]
    for f in features:
        if f not in df.columns: continue
        a=pd.to_numeric(df.loc[mask_miss,f],errors='coerce').dropna(); b=pd.to_numeric(df.loc[mask_correct_non,f],errors='coerce').dropna()
        if len(a)<3 or len(b)<3: continue
        pooled=pd.concat([a,b]); iqr=float(pooled.quantile(.75)-pooled.quantile(.25))
        gap=float(a.median()-b.median())
        ranked.append({'feature':f,'missMedian':float(a.median()),'correctMedian':float(b.median()),'medianGap':gap,'gapOverPooledIqr':(gap/iqr if abs(iqr)>1e-12 else None),'missN':int(len(a)),'correctN':int(len(b))})
    ranked.sort(key=lambda r:abs(r['gapOverPooledIqr']) if r['gapOverPooledIqr'] is not None else -1, reverse=True)
    rep={
      'reportVersion':'COORDINATION_HANDOFF_FRESH_ERROR_CLUSTERS_V0','researchOnly':True,'retrained':False,'thresholdSweep':False,
      'rows':int(len(df)),'markets':int(df.market_id.nunique()),'truthDistribution':df.label_handoff.value_counts().to_dict(),
      'predictedDistribution':df.pred.value_counts().to_dict(),'perClassRecall':recall,
      'nonSameMissedAsSame':int(mask_miss.sum()),'nonSameCorrect':int(mask_correct_non.sum()),
      'summaries':summaries,'largestDescriptiveMedianGaps':ranked[:12],
      'guard':'Descriptive audit on the same prospective cohort. Do not tune/retrain or convert these medians into runtime thresholds.'
    }
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
