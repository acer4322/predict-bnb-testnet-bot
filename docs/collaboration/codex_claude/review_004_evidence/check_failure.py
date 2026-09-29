"""Bounded read-only audit of two frozen failures; does not import a runner.

Writes only this review's derived evidence. The next public frame is inferred
from the saved successful prefix, not an observed native clock at failure.
"""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RETURNS = ROOT / 'data/research/lan_worker_returns'
PUBLIC = ROOT / 'data/research/v12g_fresh30_20260927_v24/base/inputs/public_2629019.json.gz'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
public = json.loads(gzip.decompress(PUBLIC.read_bytes()))
records = []
for job, arm in [('btc5m-v12g-fresh30-20260927-v24', 'v12g24_WB100_2629019'),
                 ('btc5m-v12g-admission-components-20260927-v30', 'v12g30_MARGIN_ONLY_2629019')]:
    directory = RETURNS / job / 'arms' / arm
    paths = [directory / name for name in ('result.json', 'failure_trace.json.gz', 'failure_receipts.json')]
    result = json.loads(paths[0].read_text(encoding='utf-8'))
    trace = json.loads(gzip.decompress(paths[1].read_bytes()))
    receipts = json.loads(paths[2].read_text(encoding='utf-8'))
    last = trace['states'][-1]
    following = [b for b in public['books'] if b['received_ms'] > last['t']]
    stack = result['traceback']
    assert 'line 290, in run_whole' in stack and 'self.process(t)' in stack
    assert 'line 31, in peek' in stack
    records.append({
        'arm': arm, 'status': result['status'], 'last_state': last,
        'state_count': len(trace['states']), 'plan_count': len(trace['plans']),
        'last_plan': trace['plans'][-1], 'last_native_action': trace['native_actions'][-1],
        'failure_in_run_whole_source_loop': True,
        'drain_in_exception_stack': 'drain_queued_responses' in stack,
        'saved_processed_receipt_deltas': receipts,
        'following_public_frames': [{k: b[k] for k in ('source_ms', 'received_ms')} for b in following],
        'actual_failure_native_clock_ns': None, 'raw_failing_receipt_rows': None,
        'causal_order_id': None,
        'source_hashes': {p.relative_to(ROOT).as_posix(): sha(p) for p in paths},
    })
batch_path = RETURNS / 'btc5m-v12g-admission-components-20260927-v30/RESULT.json'
batch = json.loads(batch_path.read_text(encoding='utf-8'))
summary_path = ROOT / 'data/research/v12g_admission_components_20260927_v30/SUMMARY.json'
summary = json.loads(summary_path.read_text(encoding='utf-8'))
result = {
    'kind': 'READ_ONLY_REVIEW_NO_NATIVE_RUN', 'market': 2629019,
    'public_source': PUBLIC.relative_to(ROOT).as_posix(), 'public_sha256': sha(PUBLIC),
    'window': public['market'], 'public_book_count': len(public['books']), 'failures': records,
    'batch': {k: batch.get(k) for k in ('status', 'paths', 'rows', 'errors', 'not_started', 'stop_reason')},
    'invalid_four_cell_interaction_present': summary.get('four_cell_interaction'),
    'review_four_cell_interaction': None,
    'review_interaction_status': 'UNKNOWN_MISSING_RESERVE_AND_NONMATCHED_COHORT',
    'missingness': {'MARGIN_ONLY': '9 complete, 1 native ERROR', 'RESERVE_ONLY': '10 NOT_STARTED'},
    'limits': ['The saved empty failure_receipts file contains processed delta rows, not the rejected journal.',
               'Actual native clock, elapse return code and raw offending receipt were not captured.',
               'Matching old/new failure sites supports a shared-path hypothesis, not unique root cause.',
               'Last saved payoff is not terminal payoff; outstanding ownership remains unverified.'],
}
out = Path(__file__).with_name('FAILURE_CHECK.json')
out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'arms': [r['arm'] for r in records], 'batch': result['batch'],
                  'four_cell_interaction': result['review_interaction_status']}, ensure_ascii=False))
