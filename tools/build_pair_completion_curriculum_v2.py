from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_pair_completion_counterfactual_v1 as cf

EPS = 1e-9
OUT_DEFAULT = ROOT / 'data/research/execution_aware_fill_lifecycle_v0/pair_completion_curriculum_v2.jsonl'


def classify(ds: list[float]) -> str:
    neg = any(x < -EPS for x in ds)
    pos = any(x > EPS for x in ds)
    if neg and not pos:
        return 'REPLACE_BETTER'
    if pos and not neg:
        return 'KEEP_BETTER'
    if not neg and not pos:
        return 'NEUTRAL'
    return 'AMBIGUOUS'


def make_row(mid: int) -> dict:
    keep = cf.run_recovery(mid, enable_intervention=False)
    replace = cf.run_recovery(mid, enable_intervention=True)
    inter = replace.get('intervention')
    if inter is None:
        return {'version':'PAIR_COMPLETION_CURRICULUM_V2','marketId':int(mid),'hasIntervention':False,'trackingLabel':'NO_STATE','features':{}}
    d5 = float(replace['targetErrorArea5s']) - float(keep['targetErrorArea5s'])
    d10 = float(replace['targetErrorArea10s']) - float(keep['targetErrorArea10s'])
    d20 = float(replace['targetErrorArea20s']) - float(keep['targetErrorArea20s'])
    label = classify([d5,d10,d20])
    return {
        'version':'PAIR_COMPLETION_CURRICULUM_V2',
        'marketId':int(mid),'checkpointMs':int(inter['atMs']),'hasIntervention':True,
        'trackingLabel':label,
        'actionMode':inter.get('actionMode'),'resolvedDuringCancel':bool(inter.get('resolvedDuringCancel')),
        'deltaTargetErrorArea5s':d5,'deltaTargetErrorArea10s':d10,'deltaTargetErrorArea20s':d20,
        'deltaTargetErrorAreaFullDiagnostic':float(replace['targetErrorAreaShareSeconds'])-float(keep['targetErrorAreaShareSeconds']),
        'deltaFinalAbsTrackingErrorDiagnostic':float(replace['finalAbsTrackingError'])-float(keep['finalAbsTrackingError']),
        'deltaPnlDiagnostic':float(replace['realizedPnl'])-float(keep['realizedPnl']),
        'baselineTargetErrorArea5s':float(keep['targetErrorArea5s']),
        'baselineTargetErrorArea10s':float(keep['targetErrorArea10s']),
        'baselineTargetErrorArea20s':float(keep['targetErrorArea20s']),
        'replaceTargetErrorArea5s':float(replace['targetErrorArea5s']),
        'replaceTargetErrorArea10s':float(replace['targetErrorArea10s']),
        'replaceTargetErrorArea20s':float(replace['targetErrorArea20s']),
        'recoveryFilledShares':float(inter.get('filledShares') or 0.0),
        'recoveryTerminalStatus':inter.get('terminalStatus'),
        'features':dict(inter.get('features') or {}),
    }


def main() -> None:
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
            print(json.dumps({'progress':i,'marketId':mid,'label':r.get('trackingLabel'),'d5':r.get('deltaTargetErrorArea5s'),'d10':r.get('deltaTargetErrorArea10s'),'d20':r.get('deltaTargetErrorArea20s'),'dFullDiagnostic':r.get('deltaTargetErrorAreaFullDiagnostic')},ensure_ascii=False),flush=True)
    counts={}
    for r in rows: counts[r.get('trackingLabel')]=counts.get(r.get('trackingLabel'),0)+1
    print(json.dumps({'ok':True,'output':str(out),'batchRows':len(rows),'counts':counts},ensure_ascii=False))

if __name__=='__main__': main()
