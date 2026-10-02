from __future__ import annotations
import argparse, importlib.util, json, os, shutil, tempfile, zipfile
from pathlib import Path
from collections import Counter

HERE = Path(__file__).resolve().parent

def sibling(name, filename):
    p = HERE / filename
    s = importlib.util.spec_from_file_location(name, p)
    if s is None or s.loader is None:
        raise ImportError(p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m

lad = sibling('safety_ladder_pair_serial_no_other', 'run_eth_safety_reintroduction_ladder_1946317.py')
LadderSim = lad.LadderSim
EPS = 1e-9

# Research-only purity change: remove the inherited <=180s no-new-exposure veto.
# Physical price/venue-min checks remain execution legality, not strategy safety gates.
lad.NO_NEW_EXPOSURE_MS = 0

CELLS = [
    ('PAIR_ONLY_1SLOT', 1, True, False),
    ('PAIR_ONLY_4SLOT', 4, True, False),
    ('PAIR_PLUS_SERIALIZATION_1SLOT', 1, True, True),
    ('PAIR_PLUS_SERIALIZATION_4SLOT', 4, True, True),
]

def summarize(rows):
    out = {}
    for cell, _, _, _ in CELLS:
        xs = [r for r in rows if r['cell'] == cell]
        pnls = [float(r['pnlDiagnosticOnly']) for r in xs]
        wins = [x for x in pnls if x > EPS]
        losses = [x for x in pnls if x < -EPS]
        submits = sum(int(r['submits']) for r in xs)
        fills = sum(int(r['fillEvents']) for r in xs)
        floors = [float(r['floor']) for r in xs]
        veto = Counter()
        for r in xs:
            for k, n in (r.get('vetoCounts') or {}).items():
                veto[k] += int(n)
        out[cell] = {
            'markets': len(xs),
            'tradeCoverage': (sum(int(r['fillEvents']) > 0 for r in xs) / len(xs)) if xs else None,
            'totalSubmits': submits,
            'totalFills': fills,
            'avgSubmitsPerMarket': submits / len(xs) if xs else None,
            'avgFillsPerMarket': fills / len(xs) if xs else None,
            'fillToSubmit': fills / submits if submits else None,
            'wins': len(wins),
            'losses': len(losses),
            'winRate': len(wins) / len(xs) if xs else None,
            'totalPnl': sum(pnls),
            'avgPnl': sum(pnls) / len(xs) if xs else None,
            'avgWin': sum(wins) / len(wins) if wins else None,
            'avgLoss': sum(losses) / len(losses) if losses else None,
            'worstLoss': min(losses) if losses else 0.0,
            'terminalFloorAvg': sum(floors) / len(floors) if floors else None,
            'terminalFloorWorst': min(floors) if floors else None,
            'vetoCounts': dict(veto),
        }
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--market-ids', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(',') if x.strip()]
    if not mids:
        raise ValueError('empty market ids')

    tmp = Path(tempfile.mkdtemp(prefix='pair_serial_no_other_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort = {int(x['marketId']): x for x in json.load(open(tmp / 'cohort.json', encoding='utf-8'))['rows']}
        rows = []
        for mid in mids:
            cr = cohort[mid]
            tape = tmp / 'tapes' / f'{mid}.json.xz'
            for cell, slots, pair, serial in CELLS:
                sim = LadderSim(tape, slots, pair, False, serial)
                try:
                    r = sim.run_ladder(cr['winner'])
                finally:
                    sim.close()
                row = {
                    'marketId': mid,
                    'winnerPostHocOnly': cr['winner'],
                    'cell': cell,
                    'maxSlots': slots,
                    'pairEconomics': pair,
                    'sameSideSerialization': serial,
                    'otherSafetyRules': False,
                    'late180Fence': False,
                    **r,
                }
                rows.append(row)
                print(json.dumps({
                    'progress': cell,
                    'marketId': mid,
                    'submits': r['submits'],
                    'fills': r['fillEvents'],
                    'pnl': r['pnlDiagnosticOnly'],
                    'floor': r['floor'],
                    'veto': r['vetoCounts'],
                }, ensure_ascii=False), flush=True)

        summary = summarize(rows)
        out = {
            'version': 'ETH_PAIR_ONLY_VS_SERIALIZATION_NO_OTHER_SAFETY_V1',
            'date': '2026-09-05',
            'researchOnly': True,
            'runtimeAuthority': False,
            'marketIds': mids,
            'rows': rows,
            'summary': summary,
            'boundary': [
                'research-only reverse-ablation; no production/runtime mutation',
                'strategy safety retained only as exact Pair economics, with matched Pair+same-side serialization comparison',
                '<=180s no-new-exposure veto disabled for this experiment',
                'floor/recoverability/ownership/generation/passive-evidence/role safety vetoes absent',
                'physical price validity and venue-min feasibility retained as execution legality only',
                'same realistic HFT tape/risk queue/250ms entry+response latency/5s TTL',
                'winner post-hoc only; no Target runtime input; no dream fill; no 8781',
            ],
        }
        op = Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'markets': len(mids), 'summary': summary}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == '__main__':
    main()
