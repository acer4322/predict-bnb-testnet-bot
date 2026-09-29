from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/r4_v0/hourly'
TIME_PRICE=['seconds_left','best_bid','best_ask','mid','spread_ticks']
STATIC=['bid_l1','ask_l1','bid_top3','ask_top3','bid_top5','ask_top5','bid_top10','ask_top10','sum_l1','min_l1','max_l1','abs_l1_imbalance','book_imb_l1','book_imb_top3','book_imb_top5','book_imb_top10','bid_levels','ask_levels','bid_l1_share_top5','ask_l1_share_top5']
FLOW=['bid_add_1s','ask_add_1s','bid_remove_1s','ask_remove_1s','bid_add_3s','ask_add_3s','bid_remove_3s','ask_remove_3s','remove_imb_1s','remove_imb_3s','add_imb_1s','add_imb_3s']

def folds(df):
    mm=df.groupby('market_id').sample_ms.min().sort_values(); ms=list(map(int,mm.index))
    starts=[90,140,190]
    return [(ms[:s],ms[s:min(s+30,len(ms))]) for s in starts if s < len(ms) and len(ms[s:min(s+30,len(ms))])>=15]

def evalset(df,label,features):
    out=[]
    for i,(trm,tem) in enumerate(folds(df)):
        tr=df[df.market_id.isin(trm)]; te=df[df.market_id.isin(tem)]
        ytr=tr[label].astype(int).to_numpy(); yte=te[label].astype(int).to_numpy()
        if len(np.unique(ytr))<2 or len(np.unique(yte))<2: continue
        model=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(max_iter=250,class_weight='balanced',C=.25,random_state=100+i))
        model.fit(tr[features],ytr); p=model.predict_proba(te[features])[:,1]
        prior=np.full(len(yte),float(np.mean(ytr)))
        out.append({'fold':i,'trainMarkets':len(trm),'testMarkets':len(tem),'testN':len(te),'positiveRate':float(np.mean(yte)),'auc':float(roc_auc_score(yte,p)),'ap':float(average_precision_score(yte,p)),'logLoss':float(log_loss(yte,p,labels=[0,1])),'priorLogLoss':float(log_loss(yte,prior,labels=[0,1]))})
    def mean(k): return float(np.mean([r[k] for r in out])) if out else None
    return {'folds':out,'meanAuc':mean('auc'),'meanAP':mean('ap'),'meanLogLoss':mean('logLoss'),'meanPriorLogLoss':mean('priorLogLoss')}

def main():
    dfs=[pd.read_csv(BASE/f'r4_target_depth_archive_hazard_v1_o{o}_m60.csv') for o in (180,120,60,0)]
    df=pd.concat(dfs,ignore_index=True).sort_values(['sample_ms','market_id']).reset_index(drop=True)
    sets={'TIME_PRICE':TIME_PRICE,'TIME_PRICE_STATIC':TIME_PRICE+STATIC,'TIME_PRICE_FLOW':TIME_PRICE+FLOW,'TIME_PRICE_FULL':TIME_PRICE+STATIC+FLOW}
    report={'version':'R4_TARGET_DEPTH_TRIGGER_FAST_ABLATION_V1','method':'blocked chronological logistic ablation across four independent 60-market archive batches','coverage':{'rows':len(df),'markets':int(df.market_id.nunique()),'minMs':int(df.sample_ms.min()),'maxMs':int(df.sample_ms.max())},'tasks':{}}
    for h in (1,2,5):
        rr={k:evalset(df,f'y{h}',v) for k,v in sets.items()}; b=rr['TIME_PRICE']
        for k,v in rr.items():
            v['deltaAucVsTimePrice']=None if v['meanAuc'] is None else v['meanAuc']-b['meanAuc']
            v['deltaAPVsTimePrice']=None if v['meanAP'] is None else v['meanAP']-b['meanAP']
            v['deltaLogLossVsTimePrice']=None if v['meanLogLoss'] is None else b['meanLogLoss']-v['meanLogLoss']
        report['tasks'][f'next{h}s']=rr
    out=BASE/'r4_target_depth_trigger_fast_ablation_20260826_v1_240m.json'; out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    short={t:{k:{'auc':round(v['meanAuc'],4),'dAUC':round(v['deltaAucVsTimePrice'],4),'dAP':round(v['deltaAPVsTimePrice'],4),'dLL':round(v['deltaLogLossVsTimePrice'],4)} for k,v in r.items()} for t,r in report['tasks'].items()}
    print(json.dumps({'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'coverage':report['coverage'],'summary':short},indent=2))
if __name__=='__main__': main()
