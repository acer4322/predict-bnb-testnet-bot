"""Isolated worker-native build coordinator; no market data or global installs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile

BUNDLE = Path(__file__).resolve().parent
ROOT = Path('C:/BTC5M-worker/.tmp/hft244_worker_repair_20260910_v1')
SOURCE = ROOT/'.tmp/hft244_accounting_source_v1'
BUILD = ROOT/'.tmp/hft244_accounting_build_v1'
OLD = Path('C:/BTC5M-worker/.tmp/hftbacktest_244/hftbacktest/_hftbacktest.cp313-win_amd64.pyd')
OLD_SHA = '74af885fe5bab4873e4672422befb0c3c5acf513ec8da0e3f80562a0c56505c6'
LOCK_SHA = '0d8fc5adf235b1f496e48ac04b0f23bec38111eae439168ee01ef147eca9fcdd'
RESULT = Path(os.environ.get('BTC5M_LAN_RESULT_DIR', str(BUNDLE/'not-dispatched')))


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def save(path, data):
    assert not path.exists(), ('immutable output exists', str(path))
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')


def unpack(name, dest):
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(BUNDLE/name) as archive:
        assert len(archive.getmembers()) < 15000
        archive.extractall(dest, filter='data')


def prepare():
    assert not ROOT.exists(), 'existing preparation must be inspected, not overwritten'
    manifest = json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
    for item in manifest['files']:
        path = BUNDLE/item['name']
        assert path.parent == BUNDLE and path.stat().st_size == item['bytes']
        assert digest(path) == item['sha256'], item['name']
    ROOT.mkdir(parents=True)
    unpack('rust.tar', ROOT/'rust')
    unpack('registry.tar', ROOT/'cargo')
    unpack('msvc.tar', ROOT/'msvc')
    unpack('source.tar', SOURCE)
    unpack('support.tar', ROOT)
    unpack('wrappers.tar', ROOT/'.tmp/hftbacktest_244/hftbacktest')
    BUILD.mkdir(parents=True)
    specs = [
        ('microsoft.windows.sdk.cpp.x64.10.0.26100.4202.nupkg', 'fe1de67646c6a6f8580669a04856b89044e08ddcf5e2b11e44cca4b8275562c9', r'^c/(um|ucrt)/x64/[^/]+\.[Ll][Ii][Bb]$'),
        ('microsoft.windows.sdk.cpp.10.0.26100.4202.nupkg', '11f7413c19ea87216a173a53e91e83fcb58f4ee7c4ca5239d4b92053ac2ea835', r'^c/Include/10\.0\.26100\.0/(ucrt|shared|um)/.*[^/]$'),
    ]
    for name, sha, pattern in specs:
        assert digest(BUNDLE/name) == sha
        with zipfile.ZipFile(BUNDLE/name) as archive:
            entries = [x for x in archive.infolist() if re.match(pattern, x.filename)]
            assert entries and sum(x.file_size for x in entries) < 600*1024**2
            for entry in entries:
                dest = (ROOT/'sdk'/entry.filename).resolve()
                assert dest.is_relative_to((ROOT/'sdk').resolve())
                archive.extract(entry, ROOT/'sdk')
    assert digest(SOURCE/'Cargo.lock') == LOCK_SHA
    # Mechanical reversal of exactly the frozen repair, for worker baseline.
    for name in ['local.rs', 'l3_local.rs']:
        path = SOURCE/'hftbacktest/src/backtest/proc'/name
        s = path.read_text()
        old = 'if order.status == Status::Filled || order.status == Status::PartiallyFilled {'
        assert s.count(old) == 1
        s = s.replace(old, 'if order.status == Status::Filled {')
        s = s.replace('// Processes receiving order response (full and partial fills).', '// Processes receiving order response.')
        path.write_text(s, encoding='utf-8', newline='\n')
    path = SOURCE/'hftbacktest/src/backtest/proc/partialfillexchange.rs'
    s = path.read_text(); assert s.count('if filled_qty >= order.leaves_qty {') == 2
    path.write_text(s.replace('if filled_qty >= order.leaves_qty {', 'if filled_qty > order.leaves_qty {'), encoding='utf-8', newline='\n')
    shutil.copy2(BUNDLE/'MANIFEST.json', ROOT/'INPUT_MANIFEST.json')
    save(ROOT/'PREPARED.json', dict(verdict='PREPARED_NOT_VALIDATED', inputManifestSha256=digest(BUNDLE/'MANIFEST.json')))


def environment():
    env = os.environ.copy()
    vc = ROOT/'msvc'; sdk = ROOT/'sdk'; rust = ROOT/'rust'
    env.update(CARGO_HOME=str(ROOT/'cargo'), CARGO_TARGET_DIR=str(ROOT/'target'),
               CARGO_INCREMENTAL='0', CARGO_BUILD_JOBS='4', PYO3_PYTHON=sys.executable,
               RUSTC=str(rust/'bin/rustc.exe'), RUSTFLAGS='--sysroot='+str(rust),
               CC=str(vc/'bin/Hostx64/x64/cl.exe'), CXX=str(vc/'bin/Hostx64/x64/cl.exe'),
               CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER=str(vc/'bin/Hostx64/x64/link.exe'),
               LIB=';'.join(str(p) for p in [vc/'lib/x64', sdk/'c/um/x64', sdk/'c/ucrt/x64']),
               INCLUDE=';'.join(str(p) for p in [vc/'include', sdk/'c/Include/10.0.26100.0/ucrt', sdk/'c/Include/10.0.26100.0/shared', sdk/'c/Include/10.0.26100.0/um']),
               PATH=str(rust/'bin')+';'+str(vc/'bin/Hostx64/x64')+';'+env['PATH'])
    return env


def bounded(label, argv, seconds):
    start = time.monotonic(); timeout = False
    with (RESULT/(label+'.stdout.log')).open('xb') as out, (RESULT/(label+'.stderr.log')).open('xb') as err:
        process = subprocess.Popen(argv, cwd=SOURCE, env=environment(), stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            rc = process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            timeout = True
            # Only the still-owned live subprocess tree, never name-wide killing.
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            rc = process.wait(timeout=10)
    info = dict(label=label, processId=process.pid, exitCode=rc, timedOut=timeout, elapsedSeconds=time.monotonic()-start)
    save(RESULT/(label+'.json'), info)
    print(json.dumps(info), flush=True)
    assert rc == 0 and not timeout, ('bounded process failed', info)


def build(variant):
    assert (ROOT/'PREPARED.json').exists() and digest(SOURCE/'Cargo.lock') == LOCK_SHA
    assert not (BUILD/(variant+'.dll')).exists()
    if variant == 'candidate':
        assert json.loads((BUILD/'baseline-smoke.json').read_text())['verdict'] == 'REBUILT_BASELINE_PARITY_PASS'
        for name in ['local.rs', 'l3_local.rs']:
            path = SOURCE/'hftbacktest/src/backtest/proc'/name; s = path.read_text()
            old = 'if order.status == Status::Filled {'; assert s.count(old) == 1
            s = s.replace(old, 'if order.status == Status::Filled || order.status == Status::PartiallyFilled {')
            s = s.replace('// Processes receiving order response.', '// Processes receiving order response (full and partial fills).')
            path.write_text(s, encoding='utf-8', newline='\n')
        path = SOURCE/'hftbacktest/src/backtest/proc/partialfillexchange.rs'; s = path.read_text()
        assert s.count('if filled_qty > order.leaves_qty {') == 2
        path.write_text(s.replace('if filled_qty > order.leaves_qty {', 'if filled_qty >= order.leaves_qty {'), encoding='utf-8', newline='\n')
    sources = {str(p.relative_to(SOURCE)): digest(p) for p in SOURCE.rglob('*') if p.is_file()}
    save(RESULT/'SOURCE_MANIFEST.json', dict(variant=variant, files=sources, lockSha256=LOCK_SHA, incremental=False, jobs=4))
    bounded(variant+'-build', [str(ROOT/'rust/bin/cargo.exe'), 'build', '--locked', '--offline', '-p', 'py-hftbacktest', '-j', '4'], 600)
    shutil.copy2(ROOT/'target/debug/hftbacktest.dll', BUILD/(variant+'.dll'))
    bounded(variant+'-smoke', [sys.executable, str(ROOT/'tools/check_hft244_rebuilt_smoke_v1.py'), '--variant', variant], 60)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('phase', choices=['prepare', 'baseline', 'candidate', 'extended'])
    phase = parser.parse_args().phase
    assert Path('C:/BTC5M-worker/.venv/Scripts/python.exe').resolve() == Path(sys.executable).resolve(), 'worker Python required'
    assert 'BTC5M_LAN_RESULT_DIR' in os.environ, 'LAN dispatcher required'
    assert digest(OLD) == OLD_SHA
    result = dict(phase=phase, marketBE=0, modelsTrained=0, freshUsed=0, promotion=False, originalSha256=OLD_SHA)
    try:
        if phase == 'prepare': prepare()
        elif phase in ['baseline', 'candidate']: build(phase)
        else: bounded('extended', [sys.executable, str(ROOT/'tools/check_hft244_extended_receipts_v1.py')], 90)
        result['verdict'] = 'PHASE_PASS'
    except Exception as exc:
        result.update(verdict='STOP_WORKER_REPAIR', error=type(exc).__name__+': '+str(exc))
    finally:
        result['originalUnchanged'] = digest(OLD) == OLD_SHA
        if not result['originalUnchanged']: result['verdict'] = 'STOP_ORIGINAL_CHANGED'
        for name in ['baseline-smoke.json', 'candidate-smoke.json', 'candidate-extended.json', 'candidate-extended-progress.json']:
            p = BUILD/name
            if p.exists(): shutil.copy2(p, RESULT/name)
        save(RESULT/'COMPACT.json', result)
        print(json.dumps(result), flush=True)
    if result['verdict'] != 'PHASE_PASS': raise SystemExit(2)


if __name__ == '__main__': main()
