"""Read frozen failure/capture; arithmetic only, never consumes a receipt."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RET = ROOT / 'data/research/lan_worker_returns'
old = RET / 'btc5m-v12g-admission-components-20260927-v30/arms/v12g30_MARGIN_ONLY_2629019/failure_trace.json.gz'
arm = RET / 'btc5m-v12g-receipt-clock-diagnostic-20260927-v31/arms/v12g31_MARGIN_ONLY_DIAG_2629019'
new = arm / 'failure_trace.json.gz'
capture = arm / 'receipt_clock_capture.json'
public = ROOT / 'data/research/v12g_fresh30_20260927_v24/base/inputs/public_2629019.json.gz'
trace = json.loads(gzip.decompress(new.read_bytes()))
cap = json.loads(capture.read_text(encoding='utf-8'))
market = json.loads(gzip.decompress(public.read_bytes()))['market']
op = trace['opportunity_first']
sent = trace['opportunity_submissions'][-1]
v = cap['violations'][0]
rows = v['rows']
assert all(r['order_id'] == 153 and r['side'] == 1 for r in rows)
assert old.read_bytes() == new.read_bytes()
before = op['state']['payoff']
at_limit = {s: before[s] - sent['price'] * sent['qty'] + (sent['qty'] if s == sent['side'] else 0)
            for s in ('UP', 'DOWN')}
receipt_cost = sum(r['qty'] * r['price'] + r['fee'] for r in rows)
receipt_qty = sum(r['qty'] for r in rows)
delta_only = {s: before[s] - receipt_cost + (receipt_qty if s == sent['side'] else 0)
              for s in ('UP', 'DOWN')}
result = {
    'scope': 'frozen 2629019, offline arithmetic; no replay or canonical receipt acceptance',
    'failure_trace_byte_identical': True,
    'window': market, 'order': sent, 'decision_reason': op['reason'],
    'passive_candidate_price': op['passive_price'],
    'fixed_passive_15_notional': 15 * op['passive_price'],
    'remaining_ms_at_submit': market['window_end_ms'] - sent['t'],
    'before_payoff': before, 'candidate_limit_cost': sent['price'] * sent['qty'],
    'candidate_full_fill_at_limit_payoff': at_limit,
    'observed_exchange_ms': [r['exchange_ts'] // 1_000_000 for r in rows],
    'observed_receive_ms': [r['receive_ts'] // 1_000_000 for r in rows],
    'exchange_after_window_ms': [r['exchange_ts'] // 1_000_000 - market['window_end_ms'] for r in rows],
    'native_clock_ns': v['native_clock_ns'],
    'receipt_clock_lead_ns': [r['receive_ts'] - v['native_clock_ns'] for r in rows],
    'raw_receipt_total_qty': receipt_qty, 'raw_receipt_cost': receipt_cost,
    'arithmetic_only_receipt_delta_payoff': delta_only,
    'not_confirmed_terminal_payoff': True,
    'advance_calls_captured': cap['advance']['n'],
    'carrier': v['responsibility']['gateway_ledger']['carriers']['UP_153'],
    'limits': ['Exchange timestamps describe this simulator, not verified real-venue expiry rules.',
               'At-limit values are decision-time candidate arithmetic, not guaranteed fills.',
               'Raw receipt arithmetic is not accepted OUR accounting or a terminal result.',
               'MARGIN_ONLY intentionally disabled reserve; this is not proof FULL took the same path.'],
    'source_hashes': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (old, new, capture, public)},
}
Path(__file__).with_name('LATE_ORDER_CHECK.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: result[k] for k in ('remaining_ms_at_submit', 'before_payoff', 'candidate_limit_cost',
                                       'candidate_full_fill_at_limit_payoff', 'raw_receipt_cost',
                                       'arithmetic_only_receipt_delta_payoff', 'advance_calls_captured')}, ensure_ascii=False))
