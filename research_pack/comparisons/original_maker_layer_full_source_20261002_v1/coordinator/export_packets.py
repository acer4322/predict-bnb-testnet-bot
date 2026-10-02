"""Read collected outputs; build immutable public packets under 45 MB each.

Full originals remain untouched. Only the established bulky
general_finite_active_rows field is omitted from the published result copy.
The canonical clock is complete; plans, native actions and direction rows are
complete. Every original file is represented by its original SHA and size.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from analyze_full import read_jobs, load

P = Path(__file__).resolve().parent
R = P.parents[2]
RET = R / 'data/research/lan_worker_returns'
MAX_BYTES = 45_000_000
FLAG = [re.compile(p, re.I) for p in (r'api[_-]?key', r'secret', r'passw(or)?d',
        r'private[_-]?key', r'authorization', r'bearer\s', r'\bsk-[a-z0-9]{16,}',
        r'mnemonic', r'seed phrase', r'BEGIN [A-Z ]*PRIVATE KEY')]
IDENTIFIER = re.compile(r'\b0x[a-fA-F0-9]{40}\b|BEGIN [A-Z ]*PRIVATE KEY')
PRIVATE_KEYS = {'wallet_address', 'account_id', 'user_id', 'transaction_hash',
                'transactionHash', 'order_hash', 'api_key', 'private_key', 'mnemonic'}


def dump(x):
    return json.dumps(x, separators=(',', ':'), allow_nan=False).encode('utf8')


def gz(x):
    return gzip.compress(dump(x), mtime=0)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def inspect_keys(x, path='$'):
    if isinstance(x, dict):
        for k, v in x.items():
            assert k not in PRIVATE_KEYS, ('PRIVATE_KEY_FIELD', path, k)
            inspect_keys(v, path + '.' + k)
    elif isinstance(x, list):
        for v in x:
            inspect_keys(v, path + '[]')


def public_check(files):
    flags = []
    for name, b in files.items():
        if name.endswith('.gz'):
            b = gzip.decompress(b)
        if name.endswith('.xz'):
            import lzma
            b = lzma.decompress(b)
        text = b.decode('utf8', 'strict')
        assert not IDENTIFIER.search(text), ('IDENTIFIER_LITERAL', name)
        if name.endswith(('.json', '.json.gz', '.json.xz')):
            inspect_keys(json.loads(text))
        hits = sorted({p.pattern for p in FLAG if p.search(text)})
        if hits:
            # Actual value review is separate; never silently allow a flagged file.
            flags.append(dict(file=name, patterns=hits, sha256=sha(files[name])))
    return flags


def finish(dest, files, index):
    assert not dest.exists(), 'Immutable packet already exists'
    files['INDEX.json'] = dump(index)
    flags = public_check(files)
    assert not flags, ('FLAGGED_REQUIRES_FILE_REVIEW', flags)
    files['PRIVATE_DATA_CHECK.json'] = dump(dict(status='PASS', FLAGGED=0,
        private_identifiers=0, inspected_files=len(files),
        provenance='Public numerical book data and isolated simulated OUR outputs; no private input tape/matches/Target data; source code literal audit separately recorded',
        check='All text and decompressed JSON scanned; explicit identifier fields and address literals rejected; schema/provenance also inspected'))
    files['SHA256SUMS.json'] = dump({'files': {n: sha(b) for n, b in files.items()}})
    assert sum(map(len, files.values())) < 50_000_000
    dest.mkdir(parents=True)
    for n, b in files.items():
        path = dest / n
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b)
    return dict(id=index['id'], path=str(dest), files=len(files), bytes=sum(map(len, files.values())),
                markets=index['markets'], status=index['status'], FLAGGED=0)


def serialize_path(j, row, job):
    arm = RET / j.get('result_job', job) / 'arms' / j['name']
    prefix = 'arms/' + j['name'] + '/'
    raw = load(arm / 'result.json')
    omitted = 0
    if isinstance(raw.get('general_finite_active_rows'), list):
        omitted = len(raw['general_finite_active_rows'])
        raw['general_finite_active_rows'] = dict(_omitted_rows=omitted,
            _note='Established research pack slimming; full raw result hash and untouched original retained')
    clock = load(arm / 'execution_clock.json') if (arm / 'execution_clock.json').exists() else {}
    trace = (json.loads(gzip.decompress((arm / 'clock_trace.json.gz').read_bytes()))
             if (arm / 'clock_trace.json.gz').exists() else {})
    sequence = {k: trace.get(k, []) for k in ('plans', 'native_actions', 'direction_rows')}
    sequence['native_call_sequence'] = [a for a in sequence['native_actions']
                                       if a['kind'] != 'CANCEL' or 'key' not in a]
    sequence['note'] = 'Native IDs/keys are local simulation identifiers; CANCEL native log includes API and keyed plan rows; native_call_sequence preserves API rows only. Times are Unix ms except canonical clock receipt timestamps in ns.'
    hashes = {f.name: dict(sha256=sha(f.read_bytes()), bytes=f.stat().st_size)
              for f in arm.iterdir() if f.is_file()}
    files = {prefix + 'result.json.gz': gz(raw),
             prefix + 'execution_clock.json.gz': gz(clock),
             prefix + 'decision_sequences.json.gz': gz(sequence),
             prefix + 'OML_METRICS.json': dump(row),
             prefix + 'RAW_FILE_HASHES.json': dump(hashes)}
    for n in ('ENGINE_SETTINGS.json', 'EXECUTION.json', 'ORCHESTRATION_AUDIT.json'):
        if (arm / n).exists():
            files[prefix + n] = (arm / n).read_bytes()
    entry = dict(market_id=j['market_id'], asset=j['asset'], arm=j['arm'], name=j['name'],
                 result_job=j.get('result_job', job), status=row['status'], winner=j['winner'],
                 result=prefix+'result.json.gz', clock=prefix+'execution_clock.json.gz',
                 sequences=prefix+'decision_sequences.json.gz', metrics=prefix+'OML_METRICS.json',
                 original_result_sha256=hashes['result.json']['sha256'], omitted_general_finite_rows=omitted)
    return files, entry


def result_packets(package):
    protocol, global_result, jobs, completed = read_jobs(package)
    report = load(P / (package.name + '_ANALYSIS.json'))
    rows = {r['name']: r for r in report['rows']}
    job = protocol['job_id']
    asset = jobs[0]['asset']
    by_market = defaultdict(list)
    for j in jobs:
        by_market[j['market_id']].append(j)
    chunks = []
    files = {}
    entries = []
    for mid, js in by_market.items():
        groupfiles = {}
        groupentries = []
        for j in js:
            if j['name'] in rows:
                pf, pe = serialize_path(j, rows[j['name']], job)
                groupfiles.update(pf)
                groupentries.append(pe)
        if files and sum(map(len, files.values())) + sum(map(len, groupfiles.values())) > MAX_BYTES:
            chunks.append((files, entries))
            files = {}
            entries = []
        files.update(groupfiles)
        entries.extend(groupentries)
    if files:
        chunks.append((files, entries))
    receipts = []
    for i, (files, entries) in enumerate(chunks, 1):
        pid = f'original_maker_layer_{asset.lower()}{len(by_market)}_results_b{i:02d}_20261002_v1'
        mids = sorted({e['market_id'] for e in entries})
        files['OFFICIAL_LABELS.json'] = dump({'records': [dict(market_id=m,
            winner=next(j['winner'] for j in jobs if j['market_id'] == m)) for m in mids],
            'source': 'Frozen official labels matched to predeclared cohort; null is official nonbinary, never inferred'})
        index = dict(id=pid, status=global_result['status'], markets=mids,
                     job_id=job, entries=entries, fits=0, live_changes=0,
                     summary_reference=f'original_maker_layer_{asset.lower()}{len(by_market)}_report_20261002_v1',
                     outputs='Canonical complete execution clock; complete plan/order/cancel/fill sequences; only general_finite_active_rows omitted from result copy with original hash')
        receipts.append(finish(P / 'packets' / pid, files, index))
    pid = f'original_maker_layer_{asset.lower()}{len(by_market)}_report_20261002_v1'
    files = {'ANALYSIS.json.gz': gz(report), 'SUMMARY.json': dump(report['summary']),
             'PROTOCOL.json': (package / 'PROTOCOL.json').read_bytes(),
             'JOBS.json.gz': gz(jobs), 'INPUTS.json.gz': gz(load(package/'INPUTS.json')),
             'WORKER_RESULT.json.gz': gz(global_result),
             'strict_completion.py': (P/'strict_completion.py').read_bytes(),
             'analyze_full.py': (P/'analyze_full.py').read_bytes()}
    if asset == 'ETH':
        files['CLASSIFICATION.json.gz'] = gz(load(P/'ETH_CLASSIFICATION.json'))
    else:
        files['CLASSIFICATION.json'] = dump({'source':'frozen cloud A FAV_TAKER',
            'records': [dict(market_id=r['market_id'], cls=r['cls'], winner=r['winner'])
                        for r in report['rows'] if r['arm']=='BASE']})
    files['PACKETS.json'] = dump(receipts)
    index = dict(id=pid, status=global_result['status'], markets=sorted(by_market),
                 job_id=job, result_packets=[r['id'] for r in receipts],
                 expected_paths=len(jobs), observed_paths=report['observed'],
                 full_cohort_gates=report['summary'], fits=0, live_changes=0,
                 shadow_or_live_authority=False)
    receipts.append(finish(P/'packets'/pid, files, index))
    (P/(asset+'_PACKETS.json')).write_bytes(dump(receipts))
    print(json.dumps(dict(asset=asset, packets=len(receipts), bytes=sum(r['bytes'] for r in receipts),
                          paths=report['observed'], status=global_result['status'], FLAGGED=0)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('package')
    a = ap.parse_args()
    result_packets(P/'stage'/a.package)


if __name__ == '__main__':
    main()
