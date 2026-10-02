from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hft_native_queue_regime_value_v1 as qv
from tools import hft_native_constrained_rank_v4 as v4

BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
EPS = 1e-9


def finite(x):
    try:
        x=float(x)
        return x if math.isfinite(x) else np.nan
    except Exception:
        return np.nan


def sweep_raw_lookup(sweep_name: str, frame: pd.DataFrame) -> dict[tuple[int,int],dict]:
    d=json.loads((BASE/sweep_name).read_text(encoding="utf-8"))
    seconds={
        (int(mid),int(cp)): finite(g.iloc[0].get("seconds_left"))
        for (mid,cp),g in frame.groupby(["market_id","checkpoint_ms"],sort=False)
    }
    out={}
    for r in d.get("rows") or []:
        key=(int(r["marketId"]),int(r["checkpointMs"]))
        out[key]={
            "secondsLeft":seconds.get(key,np.nan),
            "upBid":r.get("upBid"),"upAsk":r.get("upAsk"),
            "downBid":r.get("downBid"),"downAsk":r.get("downAsk"),
            # Not present in action-sweep artifact; frozen V4 target-regime imputer handles NaN.
            "predictReceiptAgeMs":np.nan,
        }
    return out


def build_candidates_frozen(frame: pd.DataFrame, raw_lookup: dict, model_artifact: dict) -> tuple[pd.DataFrame,dict]:
    tri=model_artifact["targetRegime"]
    rows=[]; excluded=0; cluster_counts={}
    for (market_id,checkpoint_ms), group in frame.groupby(["market_id","checkpoint_ms"],sort=True):
        key=(int(market_id),int(checkpoint_ms))
        rv=v4.hft_regime_vector(group,raw_lookup[key])
        z=tri["scaler"].transform(tri["imputer"].transform(np.asarray([rv],dtype=float)))
        cluster=int(tri["model"].predict(z)[0]); cluster_counts[str(cluster)]=cluster_counts.get(str(cluster),0)+1
        wait={name:0.0 for name in [*v4.BASE_RANK_FEATURES,*v4.TARGET_ONEHOT_FEATURES]}; wait["is_wait"]=1.0
        rows.append({"market_id":int(market_id),"checkpoint_ms":int(checkpoint_ms),"action":"WAIT","is_wait_action":True,"reward_mtm":0.0,"filled_shares":0.0,"realized_floor_delta":0.0,"target_cluster":cluster,**wait})
        for _,a in group.iterrows():
            floor_full=qv.post_floor_delta(a,str(a.side),float(a.action_price),qv.QTY)
            if floor_full < -EPS:
                excluded+=1; continue
            rows.append({"market_id":int(market_id),"checkpoint_ms":int(checkpoint_ms),"action":f"{a.side}_{int(a.action_offset)}","is_wait_action":False,"reward_mtm":float(a.mtm),"filled_shares":float(a.filled_shares),"realized_floor_delta":float(a.delta_floor_realized),"target_cluster":cluster,**v4.action_features(a,rv,cluster)})
    return pd.DataFrame(rows),{"constrainedActionRows":len(rows)-frame[["market_id","checkpoint_ms"]].drop_duplicates().shape[0],"floorConstraintExcludedActionRows":excluded,"targetClusterCounts":cluster_counts}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--collector",required=True)
    ap.add_argument("--sweep",required=True)
    ap.add_argument("--model",default="hft_native_constrained_rank_v4.joblib")
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    frame=qv.load_rows(a.collector,a.sweep)
    artifact=joblib.load(BASE/a.model)
    raw=sweep_raw_lookup(a.sweep,frame)
    candidates,meta=build_candidates_frozen(frame,raw,artifact)
    metrics=v4.evaluate("fresh_frozen_replication",frame,candidates,artifact["primary"])
    decision="NEED_MORE_DATA" if metrics["constrainedOracleRewardMtm1sUsdt"]<=EPS else "KEEP" if metrics["policyRewardMtm1sUsdt"]>EPS else "REJECT"
    rep={
        "version":"FROZEN_HFT_CONSTRAINED_RANK_V4_COLLECTOR_REPLICATION_V0",
        "researchOnly":True,"dreamFillAllowed":False,"modelRetrained":False,"thresholdSweep":False,
        "model":a.model,"collector":a.collector,"sweep":a.sweep,
        "execution":{"engine":"HftBacktest","queueModel":"risk","entryLatencyMs":1092,"responseLatencyMs":273,"reward":"realized 1s post-fill MTM"},
        "representationNote":"Frozen V4 representation reused. action-sweep artifact does not carry predictReceiptAgeMs; this one target-regime feature is NaN and handled by the already-frozen imputer. No model fit or threshold change.",
        "candidateMetadata":meta,"metrics":metrics,"decision":decision,
    }
    (BASE/a.output).write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding="utf-8")
    compact={k:v for k,v in metrics.items() if k!="rows"}
    print(json.dumps({"ok":True,"output":str(BASE/a.output),"decision":decision,"metrics":compact},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
