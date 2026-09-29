from __future__ import annotations

import bisect
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score, log_loss

import train_target_maker_taker_coordination_big_v1 as coord

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
HANDOFF=OUT/'post_taker_handoff_states_v1.csv'
MICRO_DB=ROOT/'data'/'microstructure.db'
REPORT=OUT/'handoff_future_external_path_oracle_v0_report.json'
AUG=OUT/'post_taker_handoff_future_external_path_v0.csv'

EXTERNAL=[]
for h in (1,3,5):
    EXTERNAL += [
        f'future_spot_ret_{h}s_oriented_bps', f'future_futures_ret_{h}s_oriented_bps',
        f'future_spot_queue_{h}s_oriented', f'future_futures_queue_{h}s_oriented',
        f'future_spot_taker_{h}s_oriented', f'future_futures_taker_{h}s_oriented',
        f'future_basis_{h}s_oriented_bps',
    ]
EXTERNAL += ['future_spot_range_5s_bps','future_futures_range_5s_bps']
PREDICT=[]
for h in (1,3,5):
    PREDICT += [f'future_predict_mid_delta_{h}s_oriented',f'future_direction_score_{h}s_oriented']


def ro(path:Path):
    c=sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro",uri=True,timeout=30); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def finite(v:Any)->float|None:
    try:x=float(v)
    except Exception:return None
    return x if math.isfinite(x) else None

def orient(x:float|None,side:str)->float:
    if x is None:return math.nan
    return x if side=='UP' else -x

def ret_bps(now:float|None,base:float|None,side:str)->float:
    if now is None or base is None or base==0:return math.nan
    r=(now/base-1.0)*10000.0; return r if side=='UP' else -r

def load_micro(con:sqlite3.Connection,start_ms:int,end_ms:int):
    cols=['timestamp_ns','spot_price','spot_queue_imbalance','spot_taker_imbalance_1s','futures_price','futures_queue_imbalance','futures_taker_imbalance_1s','perp_spot_basis_bps','prediction_up_mid','direction_score']
    q=f"SELECT {','.join(cols)} FROM microstructure_snapshots INDEXED BY micro_snapshots_time_idx WHERE timestamp_ns>=? AND timestamp_ns<=? ORDER BY timestamp_ns"
    times=[]; rows=[]
    for r in con.execute(q,(int(start_ms)*1_000_000,int(end_ms)*1_000_000)):
        d=dict(r); times.append(int(d.pop('timestamp_ns'))//1_000_000); rows.append(d)
    return times,rows

def at_before(times,rows,ms,max_age=1800):
    i=bisect.bisect_right(times,ms)-1
    if i<0 or ms-times[i]>max_age:return None
    return times[i],rows[i]

def metrics(y,pred,prob,classes):
    cm=confusion_matrix(y,pred,labels=classes)
    return {'n':len(y),'truthDistribution':{c:int((y==c).sum()) for c in classes},'predictedDistribution':{c:int(np.sum(pred==c)) for c in classes},'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'macroF1':float(f1_score(y,pred,labels=classes,average='macro',zero_division=0)),'logLoss':float(log_loss(y,prob,labels=classes)),'perClassRecall':{c:(float(cm[i,i]/cm[i].sum()) if cm[i].sum() else None) for i,c in enumerate(classes)},'confusionMatrix':{'labels':classes,'matrix':cm.tolist()}}

def train(df,features,name):
    sp=coord.split_markets(df); parts={k:df[df.market_id.astype(int).isin(v)].copy() for k,v in sp.items()}; m=coord.ebm(features); m.fit(coord.numeric(parts['train'],features),parts['train'].label_handoff.astype(str).tolist()); classes=[str(x) for x in m.classes_]
    rep={'features':features,'splitMarkets':{k:len(v) for k,v in sp.items()}}
    for k in ('train','validation','test'):
        x=coord.numeric(parts[k],features); y=parts[k].label_handoff.astype(str); rep[k]=metrics(y,m.predict(x),m.predict_proba(x),classes)
    rep['topTerms']=coord.top_terms(m,25); art=OUT/f'handoff_{name}.joblib'; joblib.dump({'features':features,'classes':classes,'model':m},art); rep['artifact']=str(art); return rep

def main():
    df=pd.read_csv(HANDOFF).sort_values(['market_end_ms','checkpoint_ms']).reset_index(drop=True)
    lo=int(df.checkpoint_ms.min())-2500; hi=int(df.checkpoint_ms.max())+7000
    con=ro(MICRO_DB)
    try: times,rows=load_micro(con,lo,hi)
    finally: con.close()
    out=[]; dropped=0
    for _,r in df.iterrows():
        t0=int(r.checkpoint_ms); side=str(r.intervention_side); base=at_before(times,rows,t0,1800)
        fut={h:at_before(times,rows,t0+h*1000,1800) for h in (1,3,5)}
        if base is None or any(fut[h] is None for h in (1,3,5)):
            dropped+=1; continue
        b=base[1]; d=r.to_dict(); spot0=finite(b.get('spot_price')); fut0=finite(b.get('futures_price')); pred0=finite(b.get('prediction_up_mid'))
        for h in (1,3,5):
            x=fut[h][1]; sp=finite(x.get('spot_price')); fp=finite(x.get('futures_price'))
            d[f'future_spot_ret_{h}s_oriented_bps']=ret_bps(sp,spot0,side); d[f'future_futures_ret_{h}s_oriented_bps']=ret_bps(fp,fut0,side)
            d[f'future_spot_queue_{h}s_oriented']=orient(finite(x.get('spot_queue_imbalance')),side); d[f'future_futures_queue_{h}s_oriented']=orient(finite(x.get('futures_queue_imbalance')),side)
            d[f'future_spot_taker_{h}s_oriented']=orient(finite(x.get('spot_taker_imbalance_1s')),side); d[f'future_futures_taker_{h}s_oriented']=orient(finite(x.get('futures_taker_imbalance_1s')),side)
            d[f'future_basis_{h}s_oriented_bps']=orient(finite(x.get('perp_spot_basis_bps')),side)
            p=finite(x.get('prediction_up_mid')); dp=(p-pred0) if p is not None and pred0 is not None else None; d[f'future_predict_mid_delta_{h}s_oriented']=orient(dp,side); d[f'future_direction_score_{h}s_oriented']=orient(finite(x.get('direction_score')),side)
        i0=bisect.bisect_left(times,t0); i1=bisect.bisect_right(times,t0+5000); seg=rows[i0:i1]
        sps=[finite(x.get('spot_price')) for x in seg]; sps=[x for x in sps if x is not None]; fps=[finite(x.get('futures_price')) for x in seg]; fps=[x for x in fps if x is not None]
        d['future_spot_range_5s_bps']=((max(sps)-min(sps))/spot0*10000.0) if sps and spot0 else math.nan
        d['future_futures_range_5s_bps']=((max(fps)-min(fps))/fut0*10000.0) if fps and fut0 else math.nan
        out.append(d)
    sub=pd.DataFrame(out); sub.to_csv(AUG,index=False)
    base_features=coord.HANDOFF_FEATURE_SETS['FULL']
    base=train(sub,base_features,'micro_subset_baseline_v0')
    ext=train(sub,base_features+EXTERNAL,'future_external_path_oracle_v0')
    pred=train(sub,base_features+EXTERNAL+PREDICT,'future_external_plus_predict_path_oracle_v0')
    report={'reportVersion':'HANDOFF_FUTURE_EXTERNAL_PATH_ORACLE_V0','researchOnly':True,'runtimeDeployable':False,'question':'Is 5s post-Taker handoff a sequential reactive policy whose action depends materially on public/external market evolution after Taker completion?','guard':'Future external/prediction path is diagnostic oracle only. Target future actions/events are never features. Prediction-market future path may be partially affected by Target own orders and is reported separately from external-only path.','coverage':{'rows':len(sub),'markets':int(sub.market_id.nunique()),'droppedAlignment':dropped,'microSnapshots':len(times)},'microSubsetBaseline':base,'futureExternalOnly':ext,'futureExternalPlusPredict':pred,'comparison':{}}
    for split in ('validation','test'):
        bb=float(base[split]['balancedAccuracy']); report['comparison'][split]={'baselineBalanced':bb,'externalBalanced':float(ext[split]['balancedAccuracy']),'externalLift':float(ext[split]['balancedAccuracy'])-bb,'externalPlusPredictBalanced':float(pred[split]['balancedAccuracy']),'externalPlusPredictLift':float(pred[split]['balancedAccuracy'])-bb,'baselineMacroF1':float(base[split]['macroF1']),'externalMacroF1':float(ext[split]['macroF1']),'externalPlusPredictMacroF1':float(pred[split]['macroF1'])}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
