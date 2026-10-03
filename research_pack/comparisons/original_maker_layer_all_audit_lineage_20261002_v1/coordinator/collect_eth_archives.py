"""Recover terminal results as small verified archives; never replay a cell.

The existing recursive SCP failed and its legacy collector deleted its own
temporary directory. Read the unchanged remote results, write new transport
archives outside the result directory, and preserve every recovery artifact.
There are no automatic transfer retries or recursive deletion operations.
"""
import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

P = Path(__file__).resolve().parent
ROOT = P.parents[2]
JOB = 'original-maker-layer-eth861-20261002-v1r2'
REMOTE_DIR = 'C:/BTC5M-worker/.tmp/oml_eth_collection_archives_20261003_v1'
SSH = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15']
spec = importlib.util.spec_from_file_location('lan', P.parent / 'native_engine_comparison_20261002_v1/lan_actions.py')
lan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lan)

REMOTE_CODE = r'''import hashlib,json,os,shutil,time,zipfile
from pathlib import Path
root=Path(r'C:\BTC5M-worker\.lan_worker_v1\results\original-maker-layer-eth861-20261002-v1r2').resolve()
out=Path(r'C:\BTC5M-worker\.tmp\oml_eth_collection_archives_20261003_v1').resolve()
assert root.parent == Path(r'C:\BTC5M-worker\.lan_worker_v1\results').resolve()
assert out.parent == Path(r'C:\BTC5M-worker\.tmp').resolve() and not out.exists()
assert shutil.disk_usage(out.parent).free > 8_000_000_000
out.mkdir()
(out/'INTENT.json').write_text(json.dumps(dict(job_id=root.name,attempts=1,read_only_results=True,started=time.time())))
result=json.loads((root/'RESULT.json').read_bytes())
assert result['status']=='COMPLETE_WITH_INVALID_PATHS'
paths=sorted(f for f in root.rglob('*') if f.is_file())
assert len(paths)>10000 and not any(f.is_symlink() for f in paths)
groups=[];group=[];size=0
for f in paths:
 n=f.stat().st_size
 if group and (size+n>200_000_000 or len(group)>=1000):
  groups.append(group);group=[];size=0
 group.append(f);size+=n
if group:groups.append(group)
files={};archives=[]
for i,group in enumerate(groups):
 name=f'part_{i:03d}.zip';tmp=out/(name+'.tmp')
 with zipfile.ZipFile(tmp,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
  for f in group:
   rel=f.relative_to(root).as_posix();before=f.stat()
   h=hashlib.sha256()
   with f.open('rb') as stream:
    while b:=stream.read(4*1024*1024):h.update(b)
   z.write(f,rel)
   after=f.stat();assert (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),rel
   files[rel]=dict(sha256=h.hexdigest(),bytes=before.st_size)
 final=out/name;os.replace(tmp,final)
 h=hashlib.sha256()
 with final.open('rb') as stream:
  while b:=stream.read(4*1024*1024):h.update(b)
 archives.append(dict(name=name,sha256=h.hexdigest(),bytes=final.stat().st_size,files=len(group)))
 print(json.dumps(dict(archive=name,files=len(group),bytes=final.stat().st_size,done=i+1,total=len(groups))),flush=True)
manifest=dict(job_id=root.name,status='COMPLETE_ARCHIVES',files=files,archives=archives,raw_bytes=sum(f['bytes'] for f in files.values()),source_results_unchanged=True)
(out/'FINAL_MANIFEST.json').write_text(json.dumps(manifest,separators=(',',':')))
print(json.dumps(dict(status='COMPLETE_ARCHIVES',files=len(files),raw_bytes=manifest['raw_bytes'],archives=len(archives),archive_bytes=sum(a['bytes'] for a in archives))),flush=True)
'''


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def main():
    intent = P / 'ETH_COLLECTION_RECOVERY_INTENT.json'
    assert not intent.exists(), 'Inspect previous recovery; never restart blindly'
    preflight = json.loads((P / 'ETH_COLLECTION_RECOVERY_PREFLIGHT.json').read_bytes())
    assert preflight['probe']['hostname'] == 'DESKTOP-JIERAGF'
    assert preflight['job']['state'] == 'failed'
    returns = (ROOT / 'data/research/lan_worker_returns').resolve()
    destination = (returns / JOB).resolve()
    assert destination.parent == returns and destination.is_relative_to(returns) and not destination.exists()
    lock_path = returns / '_auto_collect_locks' / (JOB + '.lock')
    assert not lock_path.exists()
    lock = lan.d._acquire_collect_lock(returns, JOB)
    archives = P / 'collection_archives_v1'
    staging = (returns / f'._collecting_recovery_{JOB}_{os.getpid()}').resolve()
    assert staging.parent == returns and staging.is_relative_to(returns) and not staging.exists()
    intent.write_text(json.dumps(dict(job_id=JOB, pid=os.getpid(), attempts=1,
        recovery='Verified small archives of already terminal unchanged outputs',
        prior_error='recursive SCP connection reset; legacy collector removed its temporary directory',
        native_submissions=0, native_replays=0, staging=str(staging), remote_archive_dir=REMOTE_DIR), indent=2))
    try:
        archives.mkdir(exist_ok=False)
        staging.mkdir(exist_ok=False)
        encoded = base64.b64encode(REMOTE_CODE.encode()).decode()
        ps = "& 'C:\\BTC5M-worker\\.venv\\Scripts\\python.exe' -u -c \"exec(__import__('base64').b64decode('" + encoded + "'))\""
        command = 'powershell -NoProfile -EncodedCommand ' + base64.b64encode(ps.encode('utf-16le')).decode()
        with (archives / 'remote_creation.stdout.log').open('w') as out, (archives / 'remote_creation.stderr.log').open('w') as err:
            cp = subprocess.run(['ssh', *SSH, 'btc5m-worker', command], stdout=out, stderr=err, timeout=1800)
        assert cp.returncode == 0, 'Remote archive creation failed/uncertain; inspect, do not replay'
        manifest_path = archives / 'FINAL_MANIFEST.json'
        cp = subprocess.run(['scp', *SSH, 'btc5m-worker:' + REMOTE_DIR + '/FINAL_MANIFEST.json', str(manifest_path)], capture_output=True, text=True, timeout=60)
        assert cp.returncode == 0, cp.stderr[-1200:]
        manifest = json.loads(manifest_path.read_bytes())
        assert manifest['job_id'] == JOB and manifest['status'] == 'COMPLETE_ARCHIVES'
        for index, archive in enumerate(manifest['archives']):
            name = archive['name']
            assert Path(name).name == name and name.endswith('.zip')
            target = archives / name
            assert not target.exists()
            print(json.dumps(dict(phase='TRANSFER', index=index+1, total=len(manifest['archives']), archive=name)), flush=True)
            cp = subprocess.run(['scp', *SSH, 'btc5m-worker:' + REMOTE_DIR + '/' + name, str(target)], capture_output=True, text=True, timeout=300)
            assert cp.returncode == 0, cp.stderr[-1200:]
            assert target.stat().st_size == archive['bytes'] and digest(target) == archive['sha256'], name
            with zipfile.ZipFile(target) as z:
                for entry in z.infolist():
                    assert not entry.is_dir()
                    rel = entry.filename
                    assert rel in manifest['files'] and '\\' not in rel
                    dest = (staging / rel).resolve()
                    assert dest.is_relative_to(staging) and not dest.exists(), rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(entry) as source, dest.open('xb') as out:
                        while chunk := source.read(4 * 1024 * 1024):
                            out.write(chunk)
                    expected = manifest['files'][rel]
                    assert dest.stat().st_size == expected['bytes'] and digest(dest) == expected['sha256'], rel
        actual = {f.relative_to(staging).as_posix() for f in staging.rglob('*') if f.is_file()}
        assert actual == set(manifest['files'])
        assert not destination.exists()
        os.replace(staging, destination)
        lan.d._write_collect_marker(returns, JOB, destination, 'verified_archive_recovery')
        receipt = dict(status='COLLECTED_HASH_VERIFIED', job_id=JOB, files=len(actual),
            raw_bytes=manifest['raw_bytes'], archives=len(manifest['archives']),
            archive_bytes=sum(a['bytes'] for a in manifest['archives']),
            archive_manifest_sha256=digest(manifest_path), native_submissions=0, native_replays=0)
        (P / 'ETH_COLLECTION_RECOVERY_RESULT.json').write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt), flush=True)
    finally:
        # Only this helper's canonical lock is removed; recovery data stays intact.
        if lock.exists():
            owner = json.loads(lock.read_bytes())
            assert owner['pid'] == os.getpid()
            lock.unlink()


if __name__ == '__main__':
    main()
