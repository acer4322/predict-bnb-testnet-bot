from __future__ import annotations
import json,warnings
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1];D=ROOT/"data/research/execution_aware_fill_lifecycle_v0";SEED=20260822
FEATURES=['side_is_up', 'order_age_ms', 'quote_price', 'status_none', 'status_new', 'status_partial', 'cum_exec_qty', 'remaining_qty', 'remaining_ratio', 'partial_fill_ratio', 'active_same_count', 'active_opp_count', 'quote_offset_ticks', 'current_bid', 'current_ask', 'current_spread_ticks', 'initial_depth', 'public_cum_depletion', 'public_depletion_ratio', 'public_any_depletion', 'maker_gross', 'maker_net', 'maker_abs_net', 'maker_imbalance_ratio', 'maker_paired_coverage', 'taker_gross', 'taker_net', 'taker_abs_net', 'taker_paired_coverage', 'combined_gross', 'combined_net', 'combined_abs_net', 'combined_imbalance_ratio', 'combined_paired_coverage', 'worst_case_floor', 'best_case_pnl', 'abs_payoff_gap', 'last_maker_age_ms', 'last_maker_up_age_ms', 'last_maker_down_age_ms', 'maker_fills_1s', 'maker_fills_5s', 'maker_fills_10s', 'maker_shares_5s', 'maker_shares_10s']
st=[]
for p in sorted(D.glob("recent_execution_placement_p*.json")): st.extend(json.loads(p.read_text(encoding="utf-8")).get("placementRows",[]))
d=pd.DataFrame(st); mids=d.groupby("market_id")["checkpoint_ms"].max().sort_values().index.astype(int).tolist();a=int(len(mids)*.70);b=int(len(mids)*.85);spl={"train":mids[:a],"validation":mids[a:b],"test":mids[b:]}
X=d[FEATURES].apply(pd.to_numeric,errors="coerce");y=d.label_fill3s.astype(int);tr=d.market_id.astype(int).isin(spl["train"])
warnings.filterwarnings("ignore")
m=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=700,early_stopping_rounds=60,min_samples_leaf=18,n_jobs=-2,random_state=SEED+3);m.fit(X.loc[tr],y.loc[tr])
def met(z):
 yy=y.loc[z];pp=m.predict_proba(X.loc[z])[:,1];return {"n":len(yy),"rate":float(yy.mean()),"predMean":float(pp.mean()),"auc":float(roc_auc_score(yy,pp)),"ap":float(average_precision_score(yy,pp)),"logLoss":float(log_loss(yy,pp,labels=[0,1]))}
metrics={k:met(d.market_id.astype(int).isin(v)) for k,v in spl.items()}
joblib.dump({"version":"R2_RECENT_PLACEMENT_FILL3S_V0","features":FEATURES,"model":m,"splits":spl,"runtimeTargetDataAllowed":False,"dreamFillAllowed":False},D/"r2_recent_placement_fill3s_v0.joblib")
(D/"r2_recent_placement_fill3s_v0_report.json").write_text(json.dumps({"metrics":metrics,"splits":spl},indent=2),encoding="utf-8")
print(json.dumps({"ok":True,"metrics":metrics}))
