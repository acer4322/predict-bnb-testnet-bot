"""V39: outcome-selected mechanism benchmarks; frozen V38, no fit or host HFT.

Reuse the existing source exporter, tape reconstruction, worker and full audits.
The selected Target paths are consumed development evidence, never a holdout.
"""
import argparse
import ast
from copy import deepcopy
import gzip
import inspect
import json
import math
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import prepare_btc5m_transfer_structural_v1 as parent
from prepare_btc5m_transfer_structural_v1 import ROOT, R, read, sha, load, once
from verify_btc5m_transfer_components_v1 import same

STEM = 'BTC5M_SUCCESS_CASE_BENCHMARK_V1_20260913'
BASE = ROOT/'.lan_worker_v1/finite_active_service_2028352_20260913_v1'
PACKAGE = ROOT/'.lan_worker_v1/success_case_benchmark_v38_20260913_v1'
SOURCE = R/'market_capsule_v1/source_bundle_success_case_v39_20260913_v1'
TARGET = R/'target_casepacks_v1/TARGET_NORMAL_SMALL_OUTCOME_PATH_AUDIT_V1_20260913.json'
SELECTED = (2084104, 1977248, 1942969)
REPLAY = (1977248, 1942969)


def dump(tag, value):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def jobs():
    out = []
    for mid, side in ((1977248, 'DOWN'), (1942969, 'UP')):
        for arm, mode in [('known', 'ORACLE_'+side), ('no_direction', 'NO_DIRECTION')]:
            out.append(dict(job_id=f'fixed15-core-loop-{mid}-success-v38-{arm.replace("_", "-")}-20260913-v1',
                market=mid, arm=arm, mode=mode, cwd='.', max_threads=4,
                argv=['.venv/Scripts/python.exe', f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
                    '--market-id', str(mid), '--mode', mode, '--money-mode', 'PARALLEL_QUANTITY',
                    '--demand-mode', 'AUTO_REPAIR', '--retention', '0', '--opportunity-mode', 'ONE_ACTIVE']))
    return out


def worker():
    import run_btc5m_transfer_structural_worker_v1 as w
    w.STEM = STEM; w.PACKAGE = PACKAGE; w.dump = dump; w.jobs = jobs
    return w


def ro(path):
    c = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    return c


def prepare():
    assert not (PACKAGE/'manifest.json').exists(), 'Do not rebuild existing frozen package'
    bm = read(BASE/'manifest.json')
    assert all(sha(BASE/n) == h for n, h in bm['files'].items())
    assert sha(BASE/'manifest.json') == '8bebe0fdf6941c84e05e18cb0652e3d2b0f0efb5ae069bf4e99cd64f77e11aa6'
    with ro(ROOT/'data/wallet_maker_book_inference.db') as c:
        quality = {str(m): dict(c.execute('SELECT * FROM maker_execution_market_quality_v1 WHERE market_id=?', (m,)).fetchone()) for m in SELECTED}
    assert quality['2084104']['quality_status'] == 'INCOMPLETE_FORWARD'
    assert all(quality[str(m)]['quality_status'] == 'COMPLETE_FORWARD_V1' for m in REPLAY)
    protocol = dict(status='PREREGISTERED_BEFORE_NEW_NATIVE', selected_cases=list(SELECTED),
        native_cases=list(REPLAY), source_quality=quality, model='Frozen V38; all 20 Python files byte-identical',
        dedup='V22 and V30 already reconstructed these Target paths and identified repair/readdition. Reuse those findings. V33 transferred V32 on four different markets; V38 only tested failure case 2028352. New falsification is unchanged V38 on the explicitly outcome-selected successful cases with matched direction/no-direction arms. No repeat of either old Target population audit or 2028352 native.',
        authorization='User accepted moving principal mechanism benchmarks to 2084104/1977248/1942969 and retaining 2028352 as failure control.',
        exclusion='2084104 retains offline Target role, excluded from native by preexisting SOURCE_GAP_GT_5S quality gate (5350 ms). No gate relaxation or outcome-based replacement.',
        hypothesis='Does the frozen manager produce partial repair followed by continued acquisition, bounded residual exposure and positive protection on successful Target market paths?',
        controls=dict(passive_new_shares=15, active_shares='variable', minimum_new_notional=1,
            cash_gates=False, active_cash_cap=None, passive_cash_cap=None, capital_cap=None,
            initial_own_inventory='flat', theta=bm['theta'], fixed_qref=bm['fixed_qref'], max_active_submits=3),
        arms=dict(known='Final observed Target net side bit only; not winner or identified constant private intention.',
            no_direction='Existing first confirmed OWN net latch; flat bootstrap has inherited physical UP asymmetry, not a learned direction predictor.'),
        target_boundary='Target paths, quantities, terminal payoffs and event times remain outside actor process. Raw public execution tape goes only to simulator.',
        selection_bias='Outcome-selected and previously inspected development cases. No typical-frequency, independent holdout or out-of-sample graduation claim.',
        sizing_boundary='Target historical filled parent sizes may differ from 15. Report raw and cost-normalized geometry separately; do not copy Target order sizes or reinterpret partial fills as NEW.',
        evaluation=['Public frame, raw/canonical receipt, owner and pending accounting',
            'Both conditional terminal payoffs, loss/gain ratio, total cost and gross activity',
            'Time-normalized path comparisons and dynamic inventory direction',
            'Partial repair then readdition versus spending already positive protection',
            'Passive and Active contributions; legal-price domain mismatch under fixed15',
            'Diagnose absent mechanisms before any retuning; engine PASS is not learning PASS'],
        maximum_native_jobs=4, max_threads=4, sequential=True, model_fits=0, parameter_search=0,
        local_native_jobs=0, charts=0, existing_failure_control='fixed15-core-loop-2028352-finite-active-service-20260913-v1',
        failure_policy='Stop on execution/accounting failure; retain artifacts and UNKNOWN fields. Continue the fixed cohort on economic weakness only. One submit per name.')
    if (R/(STEM+'_PROTOCOL.json')).exists():
        same(read(R/(STEM+'_PROTOCOL.json')), protocol)
    else:
        dump('PROTOCOL', protocol)
    if not SOURCE.exists():
        cp = subprocess.run([sys.executable, str(ROOT/'tools/export_market_capsule_source_bundle_v1.py'),
            '--market-ids', ','.join(map(str, REPLAY)), '--output', str(SOURCE)], capture_output=True, text=True, check=True)
        exported = json.loads(cp.stdout)
    else:
        # Resume after the optional DuckDB import failed, before package creation.
        # The completed bounded export is reused, never overwritten or requeried.
        assert (SOURCE/'manifest.json').exists()
        exported = dict(reused_completed_export=True, manifest_sha256=sha(SOURCE/'manifest.json'))
    sm = read(SOURCE/'manifest.json')
    assert sm['marketIds'] == list(REPLAY)
    from build_market_capsule_v1 import _load_jsonl, _reconstruct_tape
    tree = ast.parse((ROOT/'tools/prepare_v20_consumed_btc5_transfer5_v1.py').read_text(encoding='utf-8'))
    FEATURES = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'FEATURES' for t in n.targets)))
    publics = _load_jsonl(SOURCE/'public_snapshots.jsonl')
    actions = _load_jsonl(SOURCE/'target_actions.jsonl')
    paths = {x['market_id']: x for x in read(TARGET)['paths']}
    PACKAGE.mkdir(exist_ok=True); (PACKAGE/'inputs/tapes').mkdir(parents=True, exist_ok=True)
    for name in bm['files']:
        dst = PACKAGE/name; dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists(): assert sha(dst) == bm['files'][name], 'Partial preparation differs from frozen parent'
        else: shutil.copy2(BASE/name, dst)
    windows = []; records = []; labels = {}
    for market in sm['markets']:
        mid = market['market_id']; tape = SOURCE/f'tapes/{mid}.json.xz'
        tape_row = next(x for x in sm['tapes'] if x['marketId'] == mid)
        assert sha(tape) == tape_row['sha256'] and tape.stat().st_size == tape_row['bytes']
        # Only two bounded tapes (~0.7 MB compressed); pure source reconstruction, no simulator or policy.
        books, info = _reconstruct_tape(tape, mid)
        assert info['resetFailures'] == 0 and len(books) == market['l2_rows'] and info['matchRows'] == market['match_rows']
        out_books = []
        for b in sorted(books, key=lambda x: (x['received_ms'], x['source_ms'])):
            assert b['source_ms'] <= b['received_ms']
            # An empty terminal-side book is observed liquidity, not missing data.
            # Preserve None and empty depth exactly; do not forward-fill a price.
            out_books.append(dict(source_ms=b['source_ms'], received_ms=b['received_ms'],
                best_bid=b['best_bid'], best_ask=b['best_ask'], bids=json.loads(b['top5_bids_json']), asks=json.loads(b['top5_asks_json'])))
        ps = []
        for row in publics:
            if row['market_id'] != mid: continue
            p = {k: row[k] for k in ('id', 'sampled_at_ms', 'timestamp_ns', 'archived_at_ms')}
            s = json.loads(row['snapshot_json']); assert int(s['marketId']) == mid
            clocks = [p['sampled_at_ms'], p['timestamp_ns'], p['archived_at_ms'], s.get('sampledAtMs'), s.get('timestampNs')]
            assert all(x is not None and int(x) > 0 for x in clocks)
            p['available_ms'] = max(int(clocks[0]), (int(clocks[1])+999999)//1000000,
                int(clocks[2]), int(clocks[3]), (int(clocks[4])+999999)//1000000)
            p['features'] = {k: s.get(k) for k in FEATURES}; ps.append(p)
        assert len(ps) == sm['sourceCountsByMarket'][str(mid)]['publicSnapshots']
        aa = [x for x in actions if x['market_id'] == mid]
        assert len(aa) == paths[mid]['recorded_fill_legs'] and all(x['quote_type'] == 'BID' for x in aa)
        inv = {s: math.fsum(x['shares'] for x in aa if x['side'] == s) for s in ('UP', 'DOWN')}
        cost = math.fsum(x['shares']*x['price'] for x in aa)
        for s in inv: same(inv[s]-cost, paths[mid]['terminal']['pnlIf'+s.title()])
        side = 'UP' if inv['UP'] > inv['DOWN'] else 'DOWN'
        assert side == paths[mid]['terminal']['normalSide']
        labels[str(mid)] = dict(side=side, provenance='Offline final observed Target net; not settlement winner',
            target_path_source_sha256=sha(TARGET), inventory=inv, cost=cost)
        public = dict(market={k: market[k] for k in ('market_id', 'window_start_ms', 'window_end_ms', 'quality_status')},
            books=out_books, public=ps, tape=dict(file=f'tapes/{mid}.json.xz', sha256=sha(tape)),
            actor_input_contract='Current available public features/book, canonical OWN state and receipts only; tape is simulator input.')
        assert set(public) == {'market', 'books', 'public', 'tape', 'actor_input_contract'}
        (PACKAGE/f'inputs/public_{mid}.json.gz').write_bytes(gzip.compress(json.dumps(public, separators=(',', ':'), allow_nan=False).encode(), mtime=0))
        shutil.copy2(tape, PACKAGE/f'inputs/tapes/{mid}.json.xz')
        windows.append(dict(marketId=mid, window=[market['window_start_ms'], market['window_end_ms']]))
        records.append(dict(market=mid, books=len(books), public_rows=len(ps), tape_info=info,
            empty_bid_frames=sum(b['best_bid'] is None for b in books),
            empty_ask_frames=sum(b['best_ask'] is None for b in books),
            target_legs=len(aa), tape_sha256=sha(tape), source_quality=market['quality_status']))
    dump('OFFLINE_LABELS', dict(status='OFFLINE_ONLY', labels=labels))
    dump('WINDOWS', dict(rows=windows))
    m = deepcopy(bm)
    m.update(version=STEM, parent_manifest_sha256=sha(BASE/'manifest.json'), market=None,
        paired_markets=list(REPLAY), maximum_native_jobs=4, panel_maximum_native_jobs=4,
        selection='Explicit successful Target cases; quality gate before replay; consumed development only',
        limitations='Frozen V38 successful-case diagnostic. Outcome-selected, no holdout or fitting.',
        window_source_sha256=sha(R/(STEM+'_WINDOWS.json')),
        source_manifest_sha256=sha(SOURCE/'manifest.json'), model_fits=0, parameter_search=0)
    m['files'] = {p.relative_to(PACKAGE).as_posix(): sha(p) for p in PACKAGE.rglob('*') if p.is_file()}
    assert all(m['files'][n] == h for n, h in bm['files'].items())
    (PACKAGE/'manifest.json').write_text(json.dumps(m, indent=2)+'\n', encoding='utf-8')
    for mode in ('ORACLE_UP', 'ORACLE_DOWN', 'NO_DIRECTION'):
        compile(parent.compiled(PACKAGE, mode), 'V39_COMPILE_ONLY', 'exec')
    component = dict(status='PASS', policy_python_files_unchanged=20, inherited_files_unchanged=len(bm['files']),
        new_public_input_files=4, total_files=len(m['files']), three_mode_compile=True,
        target_actor_path_loaded=False, source_records=records, source_export=exported,
        manifest_sha256=sha(PACKAGE/'manifest.json'), model_fits=0, native_jobs=0,
        known_modes_match_offline_labels=all(j['mode']=='NO_DIRECTION' or j['mode']=='ORACLE_'+labels[str(j['market'])]['side'] for j in jobs()))
    assert component['known_modes_match_offline_labels']
    dump('COMPONENT', component); dump('WAVE', dict(jobs=jobs(), sequential=True, max_threads=4))
    dump('PROGRESS', dict(status='PREPARED_NOT_SUBMITTED', native_submissions=0))
    return component


def submit(index):
    w = worker(); j = jobs()[index]
    w.old.identity(); w.idle()
    assert read(R/(STEM+'_PREFLIGHT.json'))['status'] == 'PASS'
    assert not w.artifact(j, 'SUBMIT').exists(), 'Already attempted: status only'
    if index:
        assert read(w.artifact(jobs()[index-1], 'AUDIT'))['execution_status'] == 'PASS'
        assert read(w.artifact(jobs()[index-1], 'CONTINUATION_AUDIT'))['status'] == 'PASS'
    assert w.dispatch.cmd_status(w.HOST, j['job_id'])['state'] == 'missing'
    w.save(j, 'SUBMIT', dict(status='ATTEMPT_IN_PROGRESS', job_id=j['job_id'], attempts=1))
    out = w.dispatch.cmd_submit(w.HOST, j['argv'], '.', j['job_id'], 4, 6, 90, auto_collect=False)
    w.save(j, 'SUBMIT', out)
    dump('PROGRESS', dict(status='NATIVE_IN_PROGRESS', current_job=j['job_id'], native_submissions=index+1))
    return out


def audit(index):
    w = worker(); j = jobs()[index]
    import verify_btc5m_transfer_structural_v1 as v
    v.STEM = STEM; v.PACKAGE = PACKAGE; v.dump = dump; v.jobs = lambda: [None, j]
    # Exact existing V38 maximum (3); all structural and receipt checks preserved.
    code = once(inspect.getsource(v.inspect_job), "len(tr['coordination_submissions'])<=2", "len(tr['coordination_submissions'])<=3")
    ns = dict(v.__dict__); exec(compile(code, 'V39_REUSE_V38_FULL_AUDIT', 'exec'), ns)
    out = ns['inspect_job'](1)
    if out['execution_status'] != 'PASS':
        dump('PROGRESS', dict(status='NATIVE_FAILED_PRESERVED', failed_job=j['job_id'], economic='UNKNOWN'))
        return out
    import verify_btc5m_transfer_continuation_v1 as cv
    cv.PACKAGE = PACKAGE; cv.STEM = STEM; cv.jobs = lambda: [None, j]
    cv.dump = lambda tag, value: w.save(j, tag, value)
    code = once(inspect.getsource(cv.main), 'episode,confirmed,crossing_owners)',
        "episode,confirmed,crossing_owners,continuation=row.get('continuation',False))")
    ns = dict(cv.__dict__); exec(compile(code, 'V39_REUSE_V38_CONTINUATION_AUDIT', 'exec'), ns); ns['main']()
    return out


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=('prepare', 'preflight', 'submit', 'status', 'collect', 'audit'))
    p.add_argument('--index', type=int, default=0); a = p.parse_args()
    if a.action == 'prepare': value = prepare()
    elif a.action == 'preflight': value = worker().preflight()
    elif a.action == 'status':
        w = worker(); value = w.dispatch.cmd_status(w.HOST, jobs()[a.index]['job_id'])
    elif a.action == 'collect': value = worker().collect(a.index)
    else: value = globals()[a.action](a.index)
    print(json.dumps(value, allow_nan=False))
