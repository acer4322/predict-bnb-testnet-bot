"""Instrumentation-only recovery. Financial world, actor, objective and data unchanged."""
from pathlib import Path
import hashlib
import json
import py_compile
import shutil

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'.lan_worker_v1/minimal_student_open_funding_train_20260911_v1'
NEW=ROOT/'.lan_worker_v1/minimal_student_open_funding_train_logfix_20260911_v2'
R=ROOT/'data/research/r4_v0/p0_provenance_v1'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    assert not NEW.exists()
    failed=ROOT/'data/research/lan_worker_returns/minimal-student-open-funding-train-20260911-v1/COMPACT.json'
    f=json.loads(failed.read_text(encoding='utf-8'))
    assert f['error']=='AssertionError: bounded trace exceeded'
    assert f['native_complete']==0 and f['joint_parameter_updates']==0 and not f['evaluations']
    old=json.loads((OLD/'MANIFEST.json').read_text(encoding='utf-8'))
    for rel,m in old['files'].items():assert sha(OLD/rel)==m['sha256']
    shutil.copytree(OLD,NEW)
    p=NEW/'run_minimal_student_open_funding_train_v1.py';s=p.read_text(encoding='utf-8')
    replacements={
        ".tmp/minimal_student_open_funding_train_20260911_v1'":".tmp/minimal_student_open_funding_train_logfix_20260911_v2'",
        "'--child'],240)":"'--child'],600)",
        'self.peak_cash_requirement=0.':'self.peak_cash_requirement=0.;self.terminal_logged=set()',
        "pending_carriers=fr['authority']['carriers'],accounts=fr['authority']['accounts'],":
        "pending_carriers={k:c for k,c in fr['authority']['carriers'].items() if c['state']!='TERMINAL'},accounts=fr['authority']['accounts'],",
        "features=stable_features(ff)":"""features=stable_features(ff)
        # Terminal owners are serialized ONCE, not included in every subsequent
        # pending-owner snapshot. All native fills and cash still remain intact.
        for key,owner in fr['authority']['carriers'].items():
            if owner['state']=='TERMINAL' and key not in self.terminal_logged:
                self.emit('terminal_owner_once',dict(key=key,owner=owner,t=fr['t']))
                self.terminal_logged.add(key)""",
        'capital_cap=None)':'capital_cap=None,terminal_owner_records=len(self.terminal_logged))',
    }
    for a,b in replacements.items():
        assert s.count(a)==1,(a,s.count(a));s=s.replace(a,b)
    p.write_text(s,encoding='utf-8');py_compile.compile(str(p),doraise=True)
    stable_before=json.loads((OLD/'MANIFEST.json').read_text(encoding='utf-8'))['files']
    for rel in stable_before:
        if rel.startswith('input_') or rel.endswith('open_funding_v1.py') or rel.endswith('joint_policy_train_v1.py'):
            assert sha(OLD/rel)==sha(NEW/rel),rel
    note='''# Open funding logging-only recovery V2

Initial run21tests PASS; first native episode passed old100budget (peak demanded1675.483045) but stopped when trace exceeded32MiB. Cause: a field named pending_carriers included all historical terminal owners in every frame, repeatedly serializing them.0complete evaluations/0parameter updates; preserve failure/partial trace.

Retry retains identical funding world, actor, initialization, scoring, seeds, candidate count, market cohort and native binary. Only instrumentation changes: pending snapshots contain current nonterminal owners; every terminal owner is separately saved once, and all canonical fills/actions/states remain. No dropped training rows, outcome-dependent sampling or changed order behavior.32MiB trace bound remains. Supervisor compute timeout600s for10evaluations rather than240s; resource/thread guards remain. This is engineering allowance, no financial cap replacement. Total attempts can be11including the earlier interrupted first run, only10complete evaluations. If unresolved resource/time limits prevent completion, report partial without claiming training succeeded.
'''
    (NEW/'LOGGING_AMENDMENT.md').write_text(note,encoding='utf-8')
    ap=R/'MINIMAL_STUDENT_OPEN_FUNDING_LOGGING_AMENDMENT_V2_20260911.md'
    assert not ap.exists();ap.write_text(note,encoding='utf-8')
    meta={p.relative_to(NEW).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p))
          for p in NEW.rglob('*') if p.is_file() and p.name!='MANIFEST.json' and '__pycache__' not in p.parts}
    m=dict(old,files=meta,logging_only_retry=True,previous_result_sha256=sha(failed),
           unchanged_policy_sha256=sha(NEW/'minimal_student_open_funding_v1.py'),
           max_runtime_seconds=600,total_attempts_including_aborted=11)
    (NEW/'MANIFEST.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=NEW.relative_to(ROOT).as_posix(),manifest_sha256=sha(NEW/'MANIFEST.json'),
        policy_unchanged=True,capital_cap=None,logging_only=True)))


if __name__=='__main__':main()
