"""Small, offline audit of frozen A/C/T receipts. No HFT or runtime imports.

Endpoint mixtures below are realized-tape geometry, NOT conditional expected
value. Subtracting direct receipts is bookkeeping, NOT a no-order replay.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

SIDES = ('UP', 'DOWN')
EXPECTED_SHA = '064196cb1f21100cca75a6afc523d43713f7c23d97993f45dffdbf6293cf3635'


def close(a, b):
    if not math.isfinite(a) or not math.isfinite(b) or abs(a-b) > 1e-8:
        raise ValueError(f'accounting mismatch: {a} != {b}')


def geometry(up, down):
    if not all(math.isfinite(x) for x in (up, down)):
        raise ValueError('non-finite endpoint')
    # p is an algebraic mixture weight, not a forecast or learned authority.
    if up < 0 and down < 0:
        return {'ordering': 'T_STRICTLY_DOMINATED_ON_THIS_TAPE', 'positiveMixture': None}
    if up > 0 and down > 0:
        return {'ordering': 'T_STRICTLY_DOMINANT_ON_THIS_TAPE', 'positiveMixture': 'ALL'}
    if up == down:
        return {'ordering': 'NEUTRAL', 'positiveMixture': None}
    root = -down/(up-down)
    return {'ordering': 'TRADEOFF_OR_BOUNDARY', 'zeroAtP_UP': root,
            'positiveMixture': 'p_UP > root' if up > down else 'p_UP < root'}


def receipt_value(receipts):
    inv = dict.fromkeys(SIDES, 0.)
    cash = 0.
    for r in receipts:
        if r['side'] not in (1, -1) or not math.isfinite(r['qty']) or r['qty'] <= 0:
            raise ValueError('invalid receipt side/quantity')
        price = r['price'] if r['side'] == 1 else 1-r['price']
        if not math.isfinite(price) or not 0 <= price <= 1:
            raise ValueError('invalid receipt price')
        # Current source gross uses zero modeled fees. Never silently net it.
        if r['fee'] != 0:
            raise ValueError('nonzero modeled fees require explicit gross/net contract')
        inv['UP' if r['side'] == 1 else 'DOWN'] += r['qty']
        cash += r['qty']*price
    return {'inventory': inv, 'cost': cash,
            'endpoints': {s: inv[s]-cash for s in SIDES}}


def audit(doc):
    if doc['verdict'] != 'CONFIRMED_HANDOFF_ACTIVE_CAPTURE_SUPPORTED':
        raise ValueError('wrong capture verdict')
    if doc['freshUsed'] or doc['training']:
        raise ValueError('unexpected source lineage')
    rows = {}
    for r in doc['rows']:
        key = (r['marketId'], r['arm'])
        if key in rows or not r['correctness']:
            raise ValueError('duplicate row or failed correctness')
        rows[key] = r
        v = receipt_value(r['receipts'])
        close(v['cost'], r['cost'])
        for s in SIDES:
            close(v['endpoints'][s], r[s])
            residual = sum(x['qty'] for x in r['anatomy']['residual'] if x['side'] == s)
            close(r['anatomy']['pairedMargin']-r['anatomy']['residualCost']+residual, r[s])
    output = []
    for contrast in doc['contrasts']:
        mid = contrast['marketId']
        a, c, t = [rows[mid, arm] for arm in ('A', 'C', 'T')]
        if not (a['mark'] == c['mark'] == t['mark'] and c['ready'] == t['ready']):
            raise ValueError('prefix/intent mismatch')
        direct = receipt_value(t['directReceipts'])
        source = {r['sequence']: r for r in t['receipts']}
        direct_ids = [r['sequence'] for r in t['directReceipts']]
        if len(set(direct_ids)) != len(direct_ids):
            raise ValueError('duplicate direct receipt')
        for r in t['directReceipts']:
            if source.get(r['sequence']) != r or r['order_id'] != t['activeOrder']['n']:
                raise ValueError('direct receipt lineage mismatch')
        dc = t['cost']-c['cost']
        endpoints = {s: t[s]-c[s] for s in SIDES}
        inventory = {s: endpoints[s]+dc for s in SIDES}
        for s in SIDES:
            close(endpoints[s], contrast['T_minus_C'][s])
            close(direct['endpoints'][s], contrast['directEndpoint'][s])
            close(endpoints[s]-direct['endpoints'][s], contrast['continuationResidual'][s])
            close((c[s]-a[s])+endpoints[s], contrast['T_minus_A'][s])
        pair = t['anatomy']['pairedMargin']-c['anatomy']['pairedMargin']
        residual_cost = t['anatomy']['residualCost']-c['anatomy']['residualCost']
        residual_qty = {s: sum(x['qty'] for x in t['anatomy']['residual'] if x['side']==s)
                           -sum(x['qty'] for x in c['anatomy']['residual'] if x['side']==s)
                        for s in SIDES}
        for s in SIDES:
            close(pair-residual_cost+residual_qty[s], endpoints[s])
        output.append({'marketId': mid, 'T_minus_C': endpoints, 'deltaCash': dc,
            'deltaInventory': inventory, 'direct': direct,
            'remainingReceiptAccounting': {
                'cost': dc-direct['cost'],
                'inventory': {s: inventory[s]-direct['inventory'][s] for s in SIDES},
                'endpoints': {s: endpoints[s]-direct['endpoints'][s] for s in SIDES}},
            'fifoDelta': {'pairedMargin': pair, 'residualCost': residual_cost,
                         'residualQty': residual_qty},
            'wholeGeometry': geometry(endpoints['UP'], endpoints['DOWN']),
            'directGeometry': geometry(direct['endpoints']['UP'], direct['endpoints']['DOWN'])})
    return {'verdict': 'REALIZED_CONTINUATION_ACCOUNTING_VERIFIED_NOT_VALUE_LABELS',
            'markets': output, 'newBE': 0, 'training': False, 'runtimeAuthority': False,
            'expectedValueIdentified': False, 'promotion': False,
            'warnings': ['Fixed-tape endpoint mixtures are not strict-past expectations.',
                         'FIFO attribution and receipt subtraction are not causal mechanism isolation.',
                         'Negative expected-value insurance can still have policy risk value.',
                         'Three purposive states cannot establish generalization or train a selector.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    if args.input.stat().st_size > 1024*1024:
        raise ValueError('bounded compact input only')
    raw = args.input.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != EXPECTED_SHA:
        raise ValueError('frozen input hash mismatch')
    result = audit(json.loads(raw))
    result['sourceSha256'] = sha
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
