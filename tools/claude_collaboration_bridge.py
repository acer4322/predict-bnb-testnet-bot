"""Local Claude authentication check and one bounded read-only collaboration task.

Does not copy credentials, install software, enable schedulers, or run research jobs.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
BOX = ROOT / 'docs/collaboration/codex_claude'
MODEL = 'claude-opus-5-5'


def cli_path() -> Path:
    direct = shutil.which('claude.exe')
    if direct:
        return Path(direct)
    local = Path(os.environ['LOCALAPPDATA'])
    cache = local / 'Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/claude-code'
    candidates = list(cache.glob('*/claude.exe')) if cache.exists() else []
    if candidates:
        def version(path):
            try:
                return tuple(int(x) for x in path.parent.name.split('.'))
            except ValueError:
                return ()
        return max(candidates, key=version)
    raise RuntimeError('Claude Code executable not found. No install was attempted.')


def auth(exe: Path) -> dict:
    result = subprocess.run([str(exe), 'auth', 'status', '--json'], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=30, shell=False)
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {'loggedIn': None, 'status': 'UNREADABLE_AUTH_RESULT', 'exit_code': result.returncode}
    # Never persist or print email, organization IDs or credentials.
    keys = ('loggedIn', 'authMethod', 'apiProvider', 'apiKeySource', 'subscriptionType')
    return {**{key: raw.get(key) for key in keys}, 'exit_code': result.returncode}


def save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def run_task(exe: Path) -> int:
    identity = auth(exe)
    if identity.get('loggedIn') is not True:
        print(json.dumps({'status': 'LOGIN_REQUIRED', 'auth': identity}))
        return 2
    # This first bridge uses subscription auth only. API-funded execution needs
    # an explicit separate budget/authorization instead of silently consuming it.
    if identity.get('apiKeySource') or identity.get('authMethod') not in ('claude.ai', 'oauth'):
        print(json.dumps({'status': 'AUTH_METHOD_REQUIRES_REVIEW', 'auth': identity}))
        return 2
    lock = BOX / '.dispatch.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError('Existing dispatch lock: inspect CURRENT/runs; do not duplicate a request.')
    try:
        os.write(fd, str(os.getpid()).encode())
        current = json.loads((BOX / 'CURRENT.json').read_text(encoding='utf-8'))
        if current['task_status'] != 'NOT_DISPATCHED':
            raise RuntimeError('TASK_001 was already dispatched or is uncertain. Inspect its saved results.')
        if any((BOX / n).exists() for n in ('ACK_001.md', 'RETURN_001.md')):
            raise RuntimeError('Desktop handoff artifacts already exist. Reconcile instead of dispatching twice.')
        sid = str(uuid.uuid4())
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        output = BOX / 'runs' / (stamp + '_' + sid[:8])
        output.mkdir(parents=True, exist_ok=False)
        task = (BOX / 'TASK_001.md').read_text(encoding='utf-8')
        prompt = ('You are the executor in a Codex-led collaboration. This is the first bounded READ-ONLY task. '
                  'Use only Read/Glob/Grep. No shell, edits, credentials, raw database/tape scans, research jobs or live changes. '
                  'Read the repository AGENTS.md and docs/collaboration/codex_claude/README_ZH.md. '
                  'Return ACK and RETURN content in Traditional Chinese in your final answer; the host saves it. '
                  'Do not claim your model identity from this prompt. Stop after the requested review.\n\n' + task)
        args = [str(exe), '-p', '--model', MODEL, '--output-format', 'json',
                '--session-id', sid, '--safe-mode', '--restricted',
                '--tools', 'Read,Glob,Grep', '--allowedTools', 'Read,Glob,Grep',
                '--permission-mode', 'dontAsk', '--strict-mcp-config']
        current.update(status='CLAUDE_RUNNING_READ_ONLY', task_status='DISPATCH_STARTED',
                       session_id=sid, transport='CLAUDE_CODE_PRINT',
                       local_cli_auth_observed=identity,
                       result_dir=output.relative_to(ROOT).as_posix(), verified_model=None)
        save(BOX / 'CURRENT.json', current)
        save(output / 'REQUEST.json', {'session_id': sid, 'model': MODEL,
             'tools': ['Read', 'Glob', 'Grep'], 'task': 'TASK_001.md',
             'research_dispatch_authorized': False, 'automatic_retry': False})
        (output / 'PROMPT.txt').write_text(prompt, encoding='utf-8')
        try:
            with (output / 'stdout.json').open('w', encoding='utf-8') as out, \
                 (output / 'stderr.txt').open('w', encoding='utf-8') as err:
                result = subprocess.run(args, input=prompt, cwd=ROOT, stdout=out, stderr=err,
                                        text=True, encoding='utf-8', errors='replace',
                                        timeout=600, shell=False)
            payload = json.loads((output / 'stdout.json').read_text(encoding='utf-8'))
            used_models = list((payload.get('modelUsage') or {}).keys())
            ok = result.returncode == 0 and not payload.get('is_error') and bool(payload.get('result'))
            model_ok = bool(used_models) and all(x == MODEL or x.startswith(MODEL + '-') for x in used_models)
            current.update(status='READY_FOR_CODEX_REVIEW' if ok and model_ok else 'RESULT_REQUIRES_INSPECTION',
                           task_status='RETURNED' if ok else 'FAILED_OR_UNKNOWN',
                           observed_models=used_models, verified_model=MODEL if model_ok else None,
                           exit_code=result.returncode)
            if payload.get('result'):
                (output / 'RETURN_001.md').write_text(str(payload['result']), encoding='utf-8')
            save(BOX / 'CURRENT.json', current)
            print(json.dumps({'status': current['status'], 'session_id': sid,
                              'result_dir': current['result_dir'], 'observed_models': used_models}))
            return 0 if ok and model_ok else 3
        except BaseException as exc:
            current.update(status='DISPATCH_INTERRUPTED_OR_UNKNOWN', task_status='FAILED_OR_UNKNOWN',
                           failure_type=type(exc).__name__, automatic_retry=False)
            save(BOX / 'CURRENT.json', current)
            raise
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'login', 'run-read-only'))
    action = parser.parse_args().action
    exe = cli_path()
    if action == 'status':
        version = subprocess.run([str(exe), '--version'], capture_output=True, text=True,
                                 timeout=15, shell=False).stdout.strip()
        print(json.dumps({'executable': str(exe), 'version': version, 'auth': auth(exe)}))
        return 0
    if action == 'login':
        return subprocess.call([str(exe), 'auth', 'login'], cwd=ROOT, shell=False)
    return run_task(exe)


if __name__ == '__main__':
    raise SystemExit(main())
