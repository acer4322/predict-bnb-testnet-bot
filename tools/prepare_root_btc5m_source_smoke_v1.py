"""Build one exact, isolated consumed-market source smoke package; no HFT."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = [
    'run_eth_ms4_r2_47_bounded_core_service_favorable_recycle',
    'run_eth_ms4_r2_46_r240_core_active_credit_mechanism_ablation',
    'run_eth_ms4_r2_40_handoff_repair_credit_quarantine',
    'run_eth_ms4_r2_39_overflow_responsibility_handoff',
    'run_eth_ms4_r2_8_fanout_role_capacity_ablation',
    'run_eth_ms4_r2_6_parallel_passive_coverage',
    'run_eth_ms4_r2_2_failure_evidence_active_drain',
    'run_eth_ms4_r2_1_fillability_active_repair',
    'run_eth_ms4_r1_queue_aware_repair',
    'run_eth_role_separated_multislot_v82_safe_parallel_capacity_smoke',
    'run_eth_role_separated_multislot_v8_repair_overflow_split_smoke',
    'run_eth_role_separated_multislot_v7_monetary_credit_smoke',
    'run_eth_target_grounded_distinct_multislot_v2_smoke',
    'run_eth_dagger60_smoke_v1',
    'hftbacktest_execution_shift_audit_v0',
    'hftbacktest_execution_tape_feed_v1',
    'hftbacktest_true_match_calibration_v0',
]
FEATURES = [
    'predictUpMid', 'spotMinusStrikeBps', 'chainlinkMinusStrikeBps',
    'spotMinusChainlinkBps', 'spotReturn1sBps', 'spotReturn3sBps',
    'futuresReturn1sBps', 'futuresReturn3sBps', 'perpSpotBasisBps',
    'spotQueueImbalance', 'futuresQueueImbalance',
    'spotTakerImbalance1s', 'futuresTakerImbalance1s',
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    import duckdb
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    out = Path(a.output).resolve()
    if not out.is_relative_to(ROOT / '.lan_worker_v1') or out.exists():
        raise ValueError('require a new, task-specific directory under .lan_worker_v1')
    capsule = ROOT / 'data/research/market_capsule_v1/benchmark_50_v1'
    source = ROOT / 'data/research/market_capsule_v1/source_bundle_50_v1'
    con = duckdb.connect(config={'threads': '2', 'memory_limit': '256MB'})
    row = con.execute('SELECT market_id,window_start_ms,window_end_ms,quality_status '
                      'FROM read_parquet(?) ORDER BY window_start_ms,market_id LIMIT 1',
                      [str(capsule / 'markets.parquet')]).fetchone()
    assert row == (2022527, 1788753600000, 1788753900000, 'COMPLETE_FORWARD_V1'), row
    raw = con.execute('SELECT id,sampled_at_ms,timestamp_ns,archived_at_ms,snapshot_json '
                      'FROM read_parquet(?) WHERE market_id=2022527 ORDER BY sampled_at_ms,id',
                      [str(capsule / 'public_snapshots.parquet')]).fetchall()
    assert 0 < len(raw) <= 5000
    public = []
    for rid, sampled, ns, archived, payload in raw:
        s = json.loads(payload)
        assert int(s['marketId']) == 2022527
        times = [sampled, ns, archived, s.get('sampledAtMs'), s.get('timestampNs')]
        assert all(x is not None and int(x) > 0 for x in times)
        available = max(int(sampled), (int(ns)+999999)//1000000, int(archived),
                        int(s['sampledAtMs']), (int(s['timestampNs'])+999999)//1000000)
        public.append({'id': rid, 'availableMs': available, 'sampledMs': sampled,
                       'timestampNs': ns, 'archivedMs': archived,
                       'features': {k: s.get(k) for k in FEATURES}})
    public.sort(key=lambda x: (x['availableMs'], x['id']))
    assert len({x['availableMs'] for x in public}) == len(public)
    encoded = json.dumps(public, separators=(',', ':'), allow_nan=False).encode()
    assert len(encoded) <= 1024**2
    paths = [f'tools/{x}.py' for x in MODULES] + [
        'src/predict_bot/execution_tape_archive_v1.py',
        'tools/run_root_btc5m_source_smoke_v1.py',
    ]
    for rel in paths:
        p = ROOT / rel
        assert p.is_file() and p.stat().st_size <= 400*1024, rel
    assert sum((ROOT/p).stat().st_size for p in paths) < 1536*1024
    out.mkdir(parents=True)
    for rel in paths:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/rel, p)
    # Namespace isolation, not a copy of the unrelated tools directory.
    namespace_files = ['tools/__init__.py', 'src/__init__.py', 'src/predict_bot/__init__.py']
    for rel in namespace_files:
        (out/rel).write_text('# Isolated research namespace; no production installers.\n', encoding='utf-8')
    tape = out/'tapes/2022527.json.xz'
    tape.parent.mkdir()
    shutil.copy2(source/'tapes/2022527.json.xz', tape)
    (out/'public.json').write_bytes(encoded)
    prereg = ROOT/'data/research/r4_v0/p0_provenance_v1/ROOT_BTC5M_OWN_STATE_CASH_SOURCE_SMOKE1_PREREG_V1_20260910.md'
    shutil.copy2(prereg, out/'PREREG.md')
    artifacts = paths + namespace_files + ['tapes/2022527.json.xz', 'public.json', 'PREREG.md']
    files = {p: {'sha256': digest(out/p), 'bytes': (out/p).stat().st_size} for p in artifacts}
    assert sum(v['bytes'] for v in files.values()) < 4*1024**2
    manifest = {'version': 'ROOT_BTC5M_SOURCE_SMOKE1_V1', 'marketId': 2022527,
                'window': list(row[1:3]), 'cohort': 'existing consumed benchmark_50_v1',
                'expectedModules': MODULES, 'files': files,
                'publicRows': len(public), 'publicFeatureKeys': FEATURES,
                'sourceManifestSha256': digest(source/'manifest.json'),
                'cohortManifestSha256': digest(capsule/'markets.parquet'),
                'publicSourceSha256': digest(capsule/'public_snapshots.parquet'),
                'publicAvailabilityBoundary': 'recorded clocks only; not collector contract proof',
                'namespaceBoundary': 'empty research initializers; production installers not imported',
                'maxBE': 2, 'training': False, 'targetRuntimeInputs': False}
    (out/'MANIFEST.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'package': str(out), 'files': len(files),
                      'bytes': sum(v['bytes'] for v in files.values()),
                      'publicRows': len(public), 'manifestSha256': digest(out/'MANIFEST.json')}))


if __name__ == '__main__':
    main()
