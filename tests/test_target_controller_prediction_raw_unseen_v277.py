from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT/'tools'
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))
import analyze_target_controller_prediction_raw_unseen_v277 as mod


def test_repair_event_lower_bound_deduplicates_same_event():
    rows=[
        {'market_id':1,'sample_ms':1000,'repair_within_3s':1,'next_taker_purpose':'REPAIR','next_taker_delay_ms':3000,'next_taker_side':'UP'},
        {'market_id':1,'sample_ms':2000,'repair_within_3s':1,'next_taker_purpose':'REPAIR','next_taker_delay_ms':2000,'next_taker_side':'UP'},
        {'market_id':2,'sample_ms':5000,'repair_within_3s':1,'next_taker_purpose':'REPAIR','next_taker_delay_ms':1000,'next_taker_side':'DOWN'},
    ]
    events,markets=mod._repair_event_lower_bound(rows)
    assert len(events)==2
    assert markets=={1,2}


def test_positive_fold_summary_requires_majority_both_metrics():
    control={'folds':[]}; raw={'folds':[]}
    for i in range(5):
        control['folds'].append({'holdoutTargetMarketId':i,'status':'OK','testPositives':1,'auc':0.5,'logLoss':0.4})
        raw['folds'].append({'holdoutTargetMarketId':i,'status':'OK','testPositives':1,'auc':0.6 if i<3 else 0.4,'logLoss':0.3 if i<3 else 0.5})
    out=mod._positive_fold_summary(control,raw)
    assert out['positiveHoldoutFolds']==5
    assert out['foldsBetterOnBothAucAndLogLoss']==3
    assert out['requiredBetterFolds']==3
    assert out['relativeSignalReplicated'] is True
