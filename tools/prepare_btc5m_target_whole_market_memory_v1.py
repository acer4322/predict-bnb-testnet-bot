"""Prepare a bounded full-prefix vs recent-history diagnostic; never fits locally."""
from __future__ import annotations
import collections
import json
import math
from pathlib import Path
from audit_btc5m_target_core_loop_topology_v1 import ROOT, Ledger, read, sha, EPS

SOURCE = ROOT / '.lan_worker_v1/target_next_event_progress_20260912_v1/dataset.json'
PACKAGE = ROOT / '.lan_worker_v1/target_whole_market_memory_20260912_v1'
TOKENS = ('P', 'B', 'PB', 'D')
WINDOW = 8  # Prespecified observation horizon, not a runtime gate.


def events(actions):
    grouped = collections.defaultdict(list)
    for a in actions:
        grouped[a['event_ms']].append(a)
    ledger = Ledger(); inventory = dict(UP=0., DOWN=0.); seen = set(); out = []
    for t, aa in sorted(grouped.items()):
        qty = {s: sum(a['shares'] for a in aa if a['side'] == s) for s in inventory}
        event = ledger.process_batch(t, qty['UP'], qty['DOWN'])
        for s in inventory: inventory[s] += qty[s]
        payment = sum(p['qty'] for p in event['payments'])
        parents = {(a['side'], a['role'], a['order_hash']) for a in aa}
        out.append(dict(t=t, token=('P' if payment > EPS else '') + ('B' if event['births'] else '') or 'D',
            payment=payment, birth=int(bool(event['births'])),
            active=sum(a['shares'] for a in aa if a['role'] == 'TAKER'),
            gross=sum(qty.values()), signed=inventory['UP'] - inventory['DOWN'],
            new_parents=len(parents - seen), seen_parents=len(parents & seen)))
        seen |= parents
    return out


def summarize(history, gross, sign):
    n = len(history); total = sum(e['gross'] for e in history)
    f = {'count_log': math.log1p(n), 'missing': int(not n)}
    for token in TOKENS: f['token_' + token] = sum(e['token'] == token for e in history) / max(1, n)
    f.update(payment_share=sum(e['payment'] for e in history) / (1 + total),
        active_share=sum(e['active'] for e in history) / (1 + total),
        gross_fraction=total / (1 + gross),
        new_parent_fraction=sum(e['new_parents'] for e in history) / max(1, sum(e['new_parents'] + e['seen_parents'] for e in history)),
        aligned_fraction=sum(e['signed'] * sign > EPS for e in history) / max(1, n),
        signed_mean=sum(e['signed'] * sign for e in history) / max(1, n) / (1 + gross),
        max_gap=max((abs(e['signed']) for e in history), default=0.) / (1 + gross),
        last_gap=(history[-1]['signed'] * sign if n else 0.) / (1 + gross),
        crossing_rate=sum(a['signed'] * b['signed'] < -EPS for a, b in zip(history, history[1:])) / max(1, n - 1))
    return f


def vector(row, history, base):
    assert history[-1]['t'] == row['last_fill_bucket'] < row['anchor_ms']
    gross = math.expm1(row['features']['gross_log']); sign = 1 if row['strong_side'] == 'UP' else -1
    local = {k: row['features'][k] for k in base}
    for lag in (1, 2):
        token = history[-lag]['token'] if len(history) >= lag else 'UNKNOWN'
        for category in TOKENS + ('UNKNOWN',): local[f'lag{lag}_{category}'] = int(token == category)
    local.update({'recent_' + k: v for k, v in summarize(history[-WINDOW:], gross, sign).items()})
    older = {'older_' + k: v for k, v in summarize(history[:-WINDOW], gross, sign).items()}
    return local, older


def main():
    assert sha(SOURCE) == 'cf253cbfb955555431f4a63bdf340a0559787a3ef84350788551b6a9301c7cd3'
    original = read(SOURCE); base = original['base_features'] + original['history_features']
    by_market = collections.defaultdict(list)
    for row in original['rows']: by_market[row['market']].append(row)
    result = []; checks = 0
    for spec in original['sources']:
        path = ROOT / spec['path']; assert sha(path) == spec['sha256']; source = read(path)
        history = events(source['targetActions'])
        mid = next(r['market'] for r in original['rows'] if str(r['market']) in path.name)
        offsets = {e['t']: i + 1 for i, e in enumerate(history)}
        probes = {0, len(by_market[mid]) // 2, len(by_market[mid]) - 1}
        for i, row in enumerate(by_market[mid]):
            past = history[:offsets[row['last_fill_bucket']]]
            local, older = vector(row, past, base)
            if i in probes:
                truncated = events([a for a in source['targetActions'] if a['event_ms'] <= row['last_fill_bucket']])
                assert (local, older) == vector(row, truncated, base); checks += 1
            result.append(dict(row, local=local, older=older, history_events=len(past),
                older_last_ms=past[-WINDOW-1]['t'] if len(past) > WINDOW else None,
                memory_key=[past[-2]['token'] if len(past) > 1 else 'UNKNOWN', past[-1]['token']]))
    data = dict(version='BTC5M_TARGET_WHOLE_MARKET_MEMORY_DATA_V1', rows=result,
        local_features=list(result[0]['local']), older_features=list(result[0]['older']),
        sources=original['sources'], prior_dataset_sha256=sha(SOURCE), window=WINDOW,
        prefix_invariance_checks=checks, limitations=original['limitations'] + [
            'Whole-market memory means summarized observed prefix through anchor, never future or full final market.',
            'CURRENT already includes cumulative inventory/progress. Incremental test concerns older path beyond that state.',
            'Older summary excludes most recent eight economic buckets. Unfilled orders and private receipts remain unknown.',
            'All eight markets already consumed; market separation is not fresh certification.'])
    PACKAGE.mkdir(parents=True, exist_ok=True)
    assert not (PACKAGE / 'dataset.json').exists(), 'immutable package already exists'
    (PACKAGE / 'dataset.json').write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(dict(rows=len(result), prefix_checks=checks, local_features=len(data['local_features']), older_features=len(data['older_features']),
        rows_with_older=sum(r['older_last_ms'] is not None for r in result), dataset_sha256=sha(PACKAGE / 'dataset.json'))))


if __name__ == '__main__': main()
