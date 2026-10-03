"""Explicit, bounded retries of read-only hashing after observed SSH timeouts."""
import json
import subprocess
import sys
import time
from pathlib import Path

P = Path(__file__).resolve().parent
events = P / 'ETH_HASH_READ_RETRY_EVENTS.jsonl'
assert not events.exists()
for attempt in range(1, 7):
    cp = subprocess.run([sys.executable, str(P / 'verify_collections.py'), *sys.argv[1:]],
                        capture_output=True, text=True, encoding='utf-8', errors='replace')
    (P / f'eth_hash_read_attempt{attempt}.stdout.log').write_text(cp.stdout, encoding='utf-8')
    (P / f'eth_hash_read_attempt{attempt}.stderr.log').write_text(cp.stderr, encoding='utf-8')
    row = dict(attempt=attempt, return_code=cp.returncode, native_submissions=0, native_replays=0,
               scope='Read-only remote and local SHA256 verification', observed_at=time.time())
    with events.open('a') as f:
        f.write(json.dumps(row)+'\n')
    print(json.dumps(row), flush=True)
    if cp.returncode == 0:
        print(cp.stdout, flush=True)
        break
    network_only = any(s in cp.stderr for s in ('Connection timed out', 'Connection reset', 'Connection closed', 'TimeoutExpired'))
    assert network_only and attempt < 6, 'Non-network hash discrepancy or unavailable endpoint: inspect preserved evidence'
    time.sleep(30)
