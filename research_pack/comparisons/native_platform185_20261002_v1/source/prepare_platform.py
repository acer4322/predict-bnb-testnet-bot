"""Create new fixtures with the supplied, unchanged fixture builder. No native run."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TASK = Path(__file__).resolve().parent
PACKAGE = TASK / 'stage/native_engine_platform185_20261002_v1'
REPRO = ROOT / 'data/research/hft244_v49_repro_20261002_v1/release'
BATCHES = [
    ('feed70', 'hft244_feed_batch_20261002_v1/release', 'hft244_feed70_official_labels_20261002_v1/HFT244_FEED70_OFFICIAL_LABELS.json', 70),
    ('batch01', 'hft244_fresh100_after2807162_20261002_v1/release_batch01', 'hft244_fresh100_after2807162_20261002_v1/labels_batch01/FRESH_AFTER2807162_BATCH01_LABELS.json', 15),
    ('batch02', 'hft244_fresh100_after2807162_20261002_v1/release_batch02', 'hft244_fresh100_after2807162_20261002_v1/labels_batch02/FRESH_AFTER2807162_BATCH02_LABELS.json', 15),
    ('batch03', 'hft244_fresh85_after2807162_20261002_v1/release', 'hft244_fresh85_after2807162_20261002_v1/labels/FRESH_AFTER2807162_BATCH03_LABELS.json', 85),
]

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')

def main():
    assert not PACKAGE.exists(), 'Inspect existing package; do not overwrite.'
    PACKAGE.mkdir(parents=True)
    inputs = TASK / 'fixture_inputs'
    assert not inputs.exists()
    records, sources = [], []
    for name, directory, labels, count in BATCHES:
        batch = ROOT / 'data/research' / directory
        label = ROOT / 'data/research' / labels
        rows = json.loads(label.read_text(encoding='utf-8-sig'))['records']
        assert len(rows) == count
        subset = inputs / name / 'markets'
        subset.mkdir(parents=True)
        for row in rows:
            mid = int(row['market_id'])
            assert row['winner'] in ('UP', 'DOWN')
            source = batch / 'markets' / str(mid)
            dest = subset / str(mid)
            dest.mkdir()
            for filename in ('events.npz', 'META.json'):
                shutil.copy2(source / filename, dest / filename)
            records.append({'market_id': mid, 'winner': row['winner']})
            sources.append({'market_id': mid, 'batch': name, 'events_sha256': sha(source/'events.npz'), 'meta_sha256': sha(source/'META.json'), 'labels_sha256': sha(label)})
        cp = subprocess.run([sys.executable, str(ROOT/'tools/hft_repro/make_fixture_dirs.py'), str(subset.parent), str(PACKAGE/'fixtures'), '--copy'], capture_output=True, text=True, check=True)
        print(cp.stdout.strip())
    assert len(records) == len({r['market_id'] for r in records}) == 185
    records.sort(key=lambda r: r['market_id'])
    save(PACKAGE/'LABELS.json', {'records': records, 'inferred': False})
    save(PACKAGE/'INPUTS.json', {'source_revision': '0237417a006fab192aaa2b2556b6414bc9941bca', 'markets': sorted(sources,key=lambda r:r['market_id']), 'selection': 'exact official-labelled 70+15+15+85; no quality or outcome filtering'})
    helper = PACKAGE/'repro/strategy/runtime_scratch_CG1AT_2671717/tools'
    helper.mkdir(parents=True)
    shutil.copy2(REPRO/'strategy/runtime_scratch_CG1AT_2671717/tools/hftbacktest_execution_shift_audit_v0.py', helper/'hftbacktest_execution_shift_audit_v0.py')
    for filename in ('strategy_lab.py', 'make_fixture_dirs.py', 'compare_lab_results.py'):
        shutil.copy2(ROOT/'tools/hft_repro'/filename, PACKAGE/filename)
    shutil.copy2(ROOT/'docs/research_specs/NATIVE_ENGINE_COMPARISON_SPEC_20261002_ZH.md', PACKAGE/'SPEC_ZH.md')
    shutil.copy2(REPRO/'strategy/packages/CG1AT_fresh100a/CANDIDATE.json', PACKAGE/'CANDIDATE.json')
    shutil.copy2(REPRO/'engine/BACKEND_READBACK.json', PACKAGE/'EXPECTED_BACKEND.json')
    shutil.copy2(TASK/'platform_worker.py', PACKAGE/'platform_worker.py')
    shutil.copy2(TASK/'INPUT_AUDIT.json', PACKAGE/'INPUT_AUDIT.json')
    files = {p.relative_to(PACKAGE).as_posix(): sha(p) for p in sorted(PACKAGE.rglob('*')) if p.is_file()}
    save(PACKAGE/'MANIFEST.json', {'files': files, 'job_id':'native-engine-platform-185-20261002-v1', 'max_threads':4, 'parallel_paths':1, 'markets':185, 'strategies':['FAV_TAKER','UNDER_TAKER'], 'lab_source_sha256':sha(ROOT/'tools/hft_repro/strategy_lab.py')})
    save(TASK/'PREPARATION.json', {'package':str(PACKAGE), 'files':len(files), 'bytes':sum(p.stat().st_size for p in PACKAGE.rglob('*') if p.is_file()), 'manifest_sha256':sha(PACKAGE/'MANIFEST.json'), 'model_fits':0,'native_executed':0})
    print(json.dumps(json.loads((TASK/'PREPARATION.json').read_text())))

if __name__ == '__main__':
    main()
