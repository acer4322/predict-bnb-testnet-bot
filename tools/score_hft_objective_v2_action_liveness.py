from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path


def as_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return float(default)


def as_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return int(default)


def normalize_row(obj: dict, source: str, fallback_mid=None) -> dict:
    cand = obj.get('candidate') if isinstance(obj.get('candidate'), dict) else obj
    mid = cand.get('marketId') or obj.get('marketId') or obj.get('market_id') or fallback_mid
    pnl = cand.get('pnlDiagnosticOnly')
    if pnl is None:
        pnl = cand.get('pnl')
    if pnl is None:
        pnl = obj.get('pnlDiagnosticOnly')
    if pnl is None:
        pnl = obj.get('pnl')
    if pnl is None:
        raise KeyError(f'no PnL field in {source}')
    fills = cand.get('fills')
    if fills is None:
        fills = cand.get('actualFillEvents')
    rounds = cand.get('rounds')
    if rounds is None:
        rounds = cand.get('semanticRounds')
    if rounds is None:
        rounds = cand.get('v70dSemanticRounds')
    births = cand.get('repairParentBirths')
    completions = cand.get('repairParentCompletions')
    return {
        'marketId': mid,
        'pnl': as_float(pnl),
        'fills': as_int(fills),
        'rounds': as_int(rounds),
        'repairParentBirths': as_int(births),
        'repairParentCompletions': as_int(completions),
        'source': source,
    }


def load_rows(path: Path) -> list[dict]:
    obj = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(obj.get('rows'), list) and obj['rows'] and all(isinstance(x, dict) for x in obj['rows']):
        out = []
        for i, row in enumerate(obj['rows']):
            # Only treat as market batch if each row has market-level PnL.
            if row.get('pnlDiagnosticOnly') is None and row.get('pnl') is None and not isinstance(row.get('candidate'), dict):
                return [normalize_row(obj, str(path), path.parent.name)]
            out.append(normalize_row(row, str(path), i))
        return out
    return [normalize_row(obj, str(path), path.parent.name)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--output')
    ap.add_argument('--trade-coverage-min', type=float, default=0.75)
    ap.add_argument('--round-market-coverage-min', type=float, default=0.50)
    ap.add_argument('--repair-completion-rate-min', type=float, default=0.50)
    a = ap.parse_args()

    files: list[Path] = []
    for raw in a.paths:
        matched = [Path(x) for x in glob.glob(raw)]
        files.extend(matched if matched else [Path(raw)])

    rows: list[dict] = []
    for p in files:
        rows.extend(load_rows(p))

    n = len(rows)
    wins = [r for r in rows if r['pnl'] > 1e-12]
    losses = [r for r in rows if r['pnl'] < -1e-12]
    flats = [r for r in rows if abs(r['pnl']) <= 1e-12]
    action = [r for r in rows if r['fills'] > 0]
    zero_fill = [r for r in rows if r['fills'] <= 0]
    round_markets = [r for r in rows if r['rounds'] > 0]
    repair_markets = [r for r in rows if r['repairParentBirths'] > 0]
    stalled_repair = [r for r in repair_markets if r['repairParentCompletions'] <= 0 and r['rounds'] <= 0]

    births = sum(r['repairParentBirths'] for r in rows)
    completions = sum(r['repairParentCompletions'] for r in rows)
    trade_cov = len(action) / n if n else 0.0
    zero_fill_rate = len(zero_fill) / n if n else 0.0
    round_cov = len(round_markets) / n if n else 0.0
    completion_rate = completions / births if births else 0.0
    stalled_rate = len(stalled_repair) / len(repair_markets) if repair_markets else 0.0
    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(r['pnl'] for r in wins) / len(wins) if wins else None
    avg_loss = sum(r['pnl'] for r in losses) / len(losses) if losses else None
    worst = min((r['pnl'] for r in rows), default=None)
    agg = sum(r['pnl'] for r in rows)

    economic_gates = {
        'averageWinningMarketPnlGt2': avg_win is not None and avg_win > 2.0,
        'everyLosingMarketLossMagnitudeLt1': not losses or min(r['pnl'] for r in losses) > -1.0,
        'winRateGt50Pct': win_rate > 0.50,
    }
    liveness_gates = {
        'tradeCoverageGteFloor': trade_cov >= a.trade_coverage_min,
        'semanticRoundMarketCoverageGteFloor': round_cov >= a.round_market_coverage_min,
        'repairParentCompletionRateGteFloor': births > 0 and completion_rate >= a.repair_completion_rate_min,
    }
    out = {
        'version': 'BTC5M_HFT_OBJECTIVE_SCORER_V2_ACTION_LIVENESS',
        'markets': n,
        'wins': len(wins),
        'losses': len(losses),
        'flat': len(flats),
        'winRate': win_rate,
        'averageWinningMarketPnl': avg_win,
        'averageLosingMarketPnl': avg_loss,
        'worstMarketPnl': worst,
        'aggregatePnl': agg,
        'tradeCoverage': trade_cov,
        'zeroFillMarkets': len(zero_fill),
        'zeroFillRate': zero_fill_rate,
        'semanticRoundMarkets': len(round_markets),
        'semanticRoundMarketCoverage': round_cov,
        'totalFills': sum(r['fills'] for r in rows),
        'totalSemanticRounds': sum(r['rounds'] for r in rows),
        'repairParentBirths': births,
        'repairParentCompletions': completions,
        'repairParentCompletionRate': completion_rate,
        'repairMarkets': len(repair_markets),
        'stalledRepairMarkets': len(stalled_repair),
        'stalledRepairMarketRate': stalled_rate,
        'economicGates': economic_gates,
        'livenessGates': liveness_gates,
        'safetyOnlyNonPerformanceRule': 'zero-fill or zero-round outcomes cannot be promoted as performance success merely because floor/PnL are flat',
        'objectivePass': all(economic_gates.values()) and all(liveness_gates.values()),
        'rows': rows,
    }
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
