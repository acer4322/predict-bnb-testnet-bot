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
OUT_DEFAULT = ROOT / 'data/research/execution_aware_fill_lifecycle_v0/pair_completion_curriculum_v1.jsonl'


def make_row(mid: int) -> dict:
    keep = cf.run_recovery(mid, enable_intervention=False)
    replace = cf.run_recovery(mid, enable_intervention=True)
    inter = replace.get('intervention')
    if inter is None:
        return {
            'version': 'PAIR_COMPLETION_CURRICULUM_V1',
            'marketId': int(mid), 'hasIntervention': False,
            'trackingLabel': 'NO_STATE',
            'deltaTargetErrorArea': None,
            'deltaFinalAbsTrackingError': None,
            'deltaPnlDiagnostic': None,
            'features': {},
        }
    d_area = float(replace['targetErrorAreaShareSeconds']) - float(keep['targetErrorAreaShareSeconds'])
    d_final = float(replace['finalAbsTrackingError']) - float(keep['finalAbsTrackingError'])
    d_pnl = float(replace['realizedPnl']) - float(keep['realizedPnl'])
    if d_area < -EPS:
        label = 'REPLACE_BETTER'
    elif d_area > EPS:
        label = 'KEEP_BETTER'
    else:
        label = 'NEUTRAL'
    return {
        'version': 'PAIR_COMPLETION_CURRICULUM_V1',
        'marketId': int(mid),
        'checkpointMs': int(inter['atMs']),
        'hasIntervention': True,
        'trackingLabel': label,
        'actionMode': inter.get('actionMode'),
        'cancelRequestedAtMs': inter.get('cancelRequestedAtMs'),
        'takerSubmittedAtMs': inter.get('submittedAtMs'),
        'resolvedDuringCancel': bool(inter.get('resolvedDuringCancel')),
        'deltaTargetErrorArea': d_area,
        'deltaFinalAbsTrackingError': d_final,
        'deltaPnlDiagnostic': d_pnl,
        'baselineTargetErrorArea': float(keep['targetErrorAreaShareSeconds']),
        'replaceTargetErrorArea': float(replace['targetErrorAreaShareSeconds']),
        'baselineFinalAbsTrackingError': float(keep['finalAbsTrackingError']),
        'replaceFinalAbsTrackingError': float(replace['finalAbsTrackingError']),
        'recoveryFilledShares': float(inter.get('filledShares') or 0.0),
        'recoveryTerminalStatus': inter.get('terminalStatus'),
        'features': dict(inter.get('features') or {}),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', default=str(OUT_DEFAULT))
    ap.add_argument('--reset', action='store_true')
    args = ap.parse_args()
    mids = [int(x) for x in args.market_ids.split(',') if x.strip()]
    out = Path(args.output)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.reset and out.exists():
        out.unlink()
    rows = []
    with out.open('a', encoding='utf-8') as fh:
        for i, mid in enumerate(mids, 1):
            row = make_row(mid)
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False, allow_nan=True) + '\n')
            fh.flush()
            print(json.dumps({
                'progress': i, 'marketId': mid,
                'label': row.get('trackingLabel'),
                'actionMode': row.get('actionMode'),
                'resolvedDuringCancel': row.get('resolvedDuringCancel'),
                'deltaTargetErrorArea': row.get('deltaTargetErrorArea'),
                'deltaPnlDiagnostic': row.get('deltaPnlDiagnostic'),
            }, ensure_ascii=False), flush=True)
    counts = {}
    for r in rows:
        k = str(r.get('trackingLabel'))
        counts[k] = counts.get(k, 0) + 1
    print(json.dumps({'ok': True, 'output': str(out), 'batchRows': len(rows), 'counts': counts}, ensure_ascii=False))


if __name__ == '__main__':
    main()
