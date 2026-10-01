"""Read-only cross-machine verification after the one named job is terminal."""
import json,hashlib
from pathlib import Path
from discover import t,JOB,P

def main():
    identity=t.identity();status=t.d.cmd_status(t.HOST,JOB)
    assert status['state'] in ('succeeded','failed','runner_error','cancelled'),status['state']
    code="""import pathlib,json,hashlib
p=pathlib.Path('C:/BTC5M-worker/.lan_worker_v1/results')/JOB
print(json.dumps({f.relative_to(p).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.rglob('*')) if f.is_file()}))
""".replace('JOB',repr(JOB))
    hashes=t.remote(code)
    (P/'REMOTE_HASHES.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    out=P.parent/'lan_worker_returns'/JOB
    for name,h in hashes.items():assert hashlib.sha256((out/name).read_bytes()).hexdigest()==h,name
    result=dict(status='PASS',worker=identity,terminal_status=status,files=len(hashes),job=JOB)
    (P/'CROSS_MACHINE_VERIFIED.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(dict(status='PASS',files=len(hashes),job_state=status['state'])))
    glob=t.global_state()
    (P/'WORKER_AFTER.json').write_text(json.dumps(glob,indent=2),encoding='utf-8')
    print(json.dumps(dict(nonterminal=glob['nonterminal'],other_processes=glob['other_processes'])))

if __name__=='__main__':main()
