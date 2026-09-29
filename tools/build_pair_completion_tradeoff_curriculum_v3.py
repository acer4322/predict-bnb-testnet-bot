from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from tools import hftbacktest_pair_completion_counterfactual_v2 as cf

EPS=1e-9
OUT_DEFAULT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/pair_completion_tradeoff_curriculum_v3.jsonl'
HORIZONS=(5,10,20)


def pareto_label(dtrack:list[float], dcost:list[float])->str:
    vals=dtrack+dcost
    if all(abs(x)<=EPS for x in vals):
        return 'NEUTRAL'
    replace_no_worse=all(x<=EPS for x in vals)
    keep_no_worse=all(x>=-EPS for x in vals)
    if replace_no_worse and any(x < -EPS for x in vals):
        return 'REPLACE_DOMINATES'
    if keep_no_worse and any(x > EPS for x in vals):
        return 'KEEP_DOMINATES'
    return 'TRADEOFF'


def make_row(mid:int)->dict:
    keep=cf.run_recovery(mid,False)
    repl=cf.run_recovery(mid,True)
    inter=repl.get('intervention')
    if inter is None:
        return {'version':'PAIR_COMPLETION_TRADEOFF_CURRICULUM_V3','marketId':mid,'hasIntervention':False,'paretoLabel':'NO_STATE','features':{}}
    dtrack=[]; dcost=[]
    row={
        'version':'PAIR_COMPLETION_TRADEOFF_CURRICULUM_V3','marketId':mid,
        'checkpointMs':int(inter['atMs']),'hasIntervention':True,
        'actionMode':inter.get('actionMode'),'resolvedDuringCancel':bool(inter.get('resolvedDuringCancel')),
        'candidateSide':keep.get('candidateRecoverySide'),'candidateQty':float(keep.get('candidateQty') or 0.0),
        'candidateAsk':keep.get('candidateRecoveryAsk'),
        'candidateOriginalChildNum':keep.get('candidateOriginalChildNum'),
        'candidateChildKeepLabels':keep.get('candidateChildKeepLabels'),
        'features':dict(inter.get('features') or {}),
    }
    q=max(float(keep.get('candidateQty') or 0.0),EPS)
    for h in HORIZONS:
        kt=float(keep[f'targetErrorArea{h}s']); rt=float(repl[f'targetErrorArea{h}s'])
        kc=keep.get(f'completionCost{h}s'); rc=repl.get(f'completionCost{h}s')
        dt=rt-kt
        dc=(float(rc)-float(kc)) if rc is not None and kc is not None else None
        row[f'keepTargetErrorArea{h}s']=kt; row[f'replaceTargetErrorArea{h}s']=rt; row[f'deltaTargetErrorArea{h}s']=dt
        row[f'keepCompletionCost{h}s']=kc; row[f'replaceCompletionCost{h}s']=rc; row[f'deltaCompletionCost{h}s']=dc
        row[f'trackingGainFraction{h}s']=(-dt/max(abs(kt),1.0))
        row[f'costDeltaPerShare{h}s']=(dc/q) if dc is not None else None
        dtrack.append(dt)
        if dc is not None: dcost.append(dc)
    row['paretoLabel']=pareto_label(dtrack,dcost) if len(dcost)==3 else 'AMBIGUOUS_COST'
    row['deltaPnlDiagnostic']=(float(repl['realizedPnl'])-float(keep['realizedPnl'])) if repl.get('realizedPnl') is not None and keep.get('realizedPnl') is not None else None
    row['deltaFullTargetErrorDiagnostic']=float(repl['targetErrorAreaShareSeconds'])-float(keep['targetErrorAreaShareSeconds'])
    return row


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',default=str(OUT_DEFAULT)); ap.add_argument('--reset',action='store_true'); a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    out=Path(a.output)
    if not out.is_absolute(): out=ROOT/out
    out.parent.mkdir(parents=True,exist_ok=True)
    if a.reset and out.exists(): out.unlink()
    rows=[]
    with out.open('a',encoding='utf-8') as fh:
        for i,mid in enumerate(mids,1):
            r=make_row(mid); rows.append(r); fh.write(json.dumps(r,ensure_ascii=False,allow_nan=True)+'\n'); fh.flush()
            print(json.dumps({'progress':i,'marketId':mid,'pareto':r.get('paretoLabel'),'dTrack20':r.get('deltaTargetErrorArea20s'),'dCost20':r.get('deltaCompletionCost20s'),'gain20':r.get('trackingGainFraction20s'),'costPerShare20':r.get('costDeltaPerShare20s')},ensure_ascii=False),flush=True)
    counts={}
    for r in rows: counts[str(r.get('paretoLabel'))]=counts.get(str(r.get('paretoLabel')),0)+1
    print(json.dumps({'ok':True,'output':str(out),'batchRows':len(rows),'counts':counts},ensure_ascii=False))

if __name__=='__main__': main()
