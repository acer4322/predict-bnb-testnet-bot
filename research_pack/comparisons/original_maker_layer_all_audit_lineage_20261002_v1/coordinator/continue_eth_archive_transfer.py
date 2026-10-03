"""Continue verified immutable transport archives with explicit transfer logs.

Only missing archives are downloaded. Every attempt has a new local directory;
incomplete files survive. Retrying a read-only file transfer never invokes a
native job, strategy, service, archive builder, or frozen-result mutation.
"""
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path
from collect_eth_archives_v2 import P, ROOT, JOB, REMOTE_DIR, SSH, lan, digest


def main():
    state = json.loads((P / 'ETH_SUPERVISION_STATE.json').read_bytes())
    assert (state['state'] == 'STOPPED_FOR_INSPECTION' or
            (state['state'] == 'POSTPROCESS_RUNNING' and state['phase'] == '01_archive_collect'))
    intent_path = P / 'ETH_ARCHIVE_TRANSFER_CONTINUATION_INTENT.json'
    assert not intent_path.exists()
    previous = json.loads((P / 'ETH_COLLECTION_RECOVERY_V2_INTENT.json').read_bytes())
    returns = (ROOT / 'data/research/lan_worker_returns').resolve()
    destination = (returns / JOB).resolve()
    staging = Path(previous['staging']).resolve()
    assert staging.parent == returns and staging.name.startswith('._collecting_recovery_' + JOB + '_')
    assert staging.is_dir() and destination.parent == returns and not destination.exists()
    archives = P / 'collection_archives_v2'
    manifest_path = archives / 'FINAL_MANIFEST.json'
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest['status'] == 'COMPLETE_ARCHIVES' and manifest['job_id'] == JOB
    lock_path = returns / '_auto_collect_locks' / (JOB + '.lock')
    assert not lock_path.exists()
    lock = lan.d._acquire_collect_lock(returns, JOB)
    intent_path.write_text(json.dumps(dict(pid=os.getpid(), attempts=1, native_submissions=0,
        native_replays=0, source='Already complete SHA-pinned 63 archives',
        policy='At most 6 visibly logged read-only SCP attempts per batch; fresh attempt directory; preserve partials',
        staging=str(staging)), indent=2))
    log = P / 'ETH_ARCHIVE_TRANSFER_EVENTS.jsonl'

    def event(row):
        row['observed_at'] = time.time()
        with log.open('a', encoding='utf-8') as out:
            out.write(json.dumps(row) + '\n')
        print(json.dumps(row), flush=True)
        # Retain ownership while processing this long collection.
        assert json.loads(lock.read_bytes())['pid'] == os.getpid()
        os.utime(lock, None)

    try:
        pending = []
        for archive in manifest['archives']:
            f = archives / archive['name']
            if f.exists():
                assert f.stat().st_size == archive['bytes'] and digest(f) == archive['sha256'], f.name
            else:
                pending.append(archive)
        event(dict(phase='RESUME', verified_archives=len(manifest['archives'])-len(pending), pending=len(pending)))
        batches = [pending[i:i+8] for i in range(0, len(pending), 8)]
        for batch_index, batch in enumerate(batches):
            missing = list(batch)
            for attempt in range(1, 7):
                if not missing:
                    break
                tmp = P / f'archive_transfer_b{batch_index:02d}_attempt{attempt}'
                tmp.mkdir(exist_ok=False)
                event(dict(phase='TRANSFER', batch=batch_index+1, batches=len(batches), attempt=attempt,
                           archives=[a['name'] for a in missing]))
                sources = ['btc5m-worker:' + REMOTE_DIR + '/' + a['name'] for a in missing]
                command = ['scp', *SSH, *sources, str(tmp)]
                assert len(subprocess.list2cmdline(command)) < 8191
                try:
                    cp = subprocess.run(command, capture_output=True, text=True, timeout=480)
                    code, error = cp.returncode, cp.stderr[-1600:]
                except subprocess.TimeoutExpired as exc:
                    code, error = 'TIMEOUT', str(exc)[:1600]
                remaining = []
                for archive in missing:
                    f = tmp / archive['name']
                    if f.exists() and f.stat().st_size == archive['bytes'] and digest(f) == archive['sha256']:
                        target = archives / archive['name']
                        assert not target.exists()
                        os.replace(f, target)
                    else:
                        remaining.append(archive)
                event(dict(phase='TRANSFER_RESULT', batch=batch_index+1, attempt=attempt,
                           return_code=code, error=error, remaining=[a['name'] for a in remaining]))
                missing = remaining
                if missing:
                    assert attempt < 6, 'Transfer unavailable; partials retained, no native retry'
                    time.sleep(30)
            assert not missing
        for index, archive in enumerate(manifest['archives']):
            path = archives / archive['name']
            assert path.stat().st_size == archive['bytes'] and digest(path) == archive['sha256']
            with zipfile.ZipFile(path) as z:
                for entry in z.infolist():
                    assert not entry.is_dir()
                    rel = entry.filename
                    assert rel in manifest['files'] and '\\' not in rel
                    dest = (staging / rel).resolve()
                    assert dest.is_relative_to(staging)
                    expected = manifest['files'][rel]
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(entry) as source, dest.open('xb') as out:
                            while chunk := source.read(4*1024*1024):
                                out.write(chunk)
                    assert dest.stat().st_size == expected['bytes'] and digest(dest) == expected['sha256'], rel
            event(dict(phase='EXTRACT_VERIFIED', archive=index+1, total=len(manifest['archives'])))
        actual = {f.relative_to(staging).as_posix() for f in staging.rglob('*') if f.is_file()}
        assert actual == set(manifest['files']) and not destination.exists()
        os.replace(staging, destination)
        lan.d._write_collect_marker(returns, JOB, destination, 'verified_archive_recovery_continuation')
        result = dict(status='COLLECTED_HASH_VERIFIED', job_id=JOB, files=len(actual),
            raw_bytes=manifest['raw_bytes'], archives=len(manifest['archives']),
            archive_bytes=sum(a['bytes'] for a in manifest['archives']),
            archive_manifest_sha256=digest(manifest_path), native_submissions=0, native_replays=0,
            initial_scp_errors_retained=True, transfer_events=log.name)
        (P / 'ETH_COLLECTION_RECOVERY_RESULT.json').write_text(json.dumps(result, indent=2))
        event(result)
    finally:
        if lock.exists():
            assert json.loads(lock.read_bytes())['pid'] == os.getpid()
            lock.unlink()


if __name__ == '__main__':
    main()
