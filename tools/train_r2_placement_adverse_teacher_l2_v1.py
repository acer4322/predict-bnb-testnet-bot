from __future__ import annotations
import json, math, sys
from pathlib import Path
import pandas as pd, numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.strategy_book_receipt_frontier_replay_v1 import ReceiptFrontierBookTailer
from src.predict_bot import unified_controller_paper_v2 as mod
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=OUT/'r2_placement_adverse_teacher_v0_dataset.csv'
REPORT=OUT/'r2_placement_adverse_teacher_l2_v1_report.json'
AUG=OUT/'r2_placement_adverse_teacher_l2_v1_dataset.csv'
BASE=['side_is_up','reason_is_burst','quote_price','side_bid','side_ask','spread_ticks','quote_offset_ticks','secondsLeft','directionScore','spotReturn1sBps','spotReturn3sBps','spotQueueImbalance','spotTakerImbalance1s','futuresReturn1sBps','futuresReturn3sBps','futuresQueueImbalance','futuresTakerImbalance1s','predictUpMid','spotMinusStrikeBps','chainlinkMinusStrikeBps','direction_toward_side','spot1_toward_side','spot3_toward_side','spot_queue_toward_side','spot_taker_toward_side','fut1_toward_side','fut3_toward_side','fut_queue_toward_side','fut_taker_toward_side']
L2=['l2_quote_visible_depth','l2_side_best_bid_depth','l2_side_best_ask_depth','l2_side_top_imbalance','l2_native_best_bid_depth','l2_native_best_ask_depth','l2_native_top_imbalance','l2_same_depth_3t','l2_opp_depth_3t','l2_depth_ratio_3t']

def fv(x):
    try:
        v=float(x); return v if math.isfinite(v) else np.nan
    except: return np.nan

def add_l2(g:pd.DataFrame)->pd.DataFrame:
    mid=int(g.marketId.iloc[0]); tail=ReceiptFrontierBookTailer(mid,mod.VERSION,strict_hash=True); rows=[]
    try:
        for _,r in g.sort_values('placedAtMs').iterrows():
            t=int(r.placedAtMs); tail.advance(mid,t); b=tail.book or {}; bids={float(k):float(v) for k,v in (b.get('bids') or {}).items()}; asks={float(k):float(v) for k,v in (b.get('asks') or {}).items()}
            bb=max(bids) if bids else np.nan; ba=min(asks) if asks else np.nan; bbd=bids.get(bb,np.nan) if math.isfinite(bb) else np.nan; bad=asks.get(ba,np.nan) if math.isfinite(ba) else np.nan
            side=str(r.side).upper(); qp=float(r.quote_price)
            if side=='UP':
                native_px=qp; qd=bids.get(round(native_px,2),bids.get(native_px,0.0)); sbid=bbd; sask=bad
                same=sum(v for p,v in bids.items() if math.isfinite(bb) and p>=bb-0.02); opp=sum(v for p,v in asks.items() if math.isfinite(ba) and p<=ba+0.02)
            else:
                native_px=round(1.0-qp,2); qd=asks.get(native_px,0.0); sbid=bad; sask=bbd
                same=sum(v for p,v in asks.items() if math.isfinite(ba) and p<=ba+0.02); opp=sum(v for p,v in bids.items() if math.isfinite(bb) and p>=bb-0.02)
            rec=r.to_dict(); rec.update({'l2_quote_visible_depth':qd,'l2_side_best_bid_depth':sbid,'l2_side_best_ask_depth':sask,'l2_side_top_imbalance':(sbid-sask)/(sbid+sask) if fv(sbid+sask)>0 else np.nan,'l2_native_best_bid_depth':bbd,'l2_native_best_ask_depth':bad,'l2_native_top_imbalance':(bbd-bad)/(bbd+bad) if fv(bbd+bad)>0 else np.nan,'l2_same_depth_3t':same,'l2_opp_depth_3t':opp,'l2_depth_ratio_3t':same/(opp+1e-9)})
            rows.append(rec)
    finally: tail.close()
    return pd.DataFrame(rows)

def fit_eval(df,features):
    tr=df[(df.cohort=='train') & df.labelAdverse1s.notna()].copy(); model=HistGradientBoostingClassifier(max_depth=3,max_iter=80,learning_rate=.05,l2_regularization=2.0,random_state=20260822).fit(tr[features].replace([np.inf,-np.inf],np.nan).fillna(0),tr.labelAdverse1s.astype(int))
    res={}
    for c in ['train','validation','forward']:
        d=df[(df.cohort==c)&df.labelAdverse1s.notna()].copy(); y=d.labelAdverse1s.astype(int); p=model.predict_proba(d[features].replace([np.inf,-np.inf],np.nan).fillna(0))[:,1]
        res[c]={'n':len(d),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
    return res

def main():
    d=pd.read_csv(SRC); out=[]
    for mid,g in d.groupby('marketId',sort=False): out.append(add_l2(g))
    x=pd.concat(out,ignore_index=True); x.to_csv(AUG,index=False)
    rep={'version':'R2_PLACEMENT_ADVERSE_TEACHER_L2_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'dreamFillAllowed':False,'rows':len(x),'markets':int(x.marketId.nunique()),'featuresAdded':L2,'baseMetrics':fit_eval(x,BASE),'l2Metrics':fit_eval(x,BASE+L2),'guardrails':['Same pre-registered train/validation/forward cohort labels as V0','Receipt-frontier public book only at placedAtMs','No winner/Target/PnL/future input','Future +1s markout used only as training label','No threshold sweep']}
    REPORT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
