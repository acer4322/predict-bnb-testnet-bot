"""Pinned audit-only correction; continue only V56's unstarted paths."""
import hashlib,json,shutil
from pathlib import Path
R=Path(__file__).resolve().parents[1]/'data/research';S=R/'v12g_small300_demand_scale_20260928_v56';P=R/'v12g_small300_demand_scale_audit_resume_20260928_v56r1'
read=lambda p:json.loads(p.read_text(encoding='utf8'));sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,ensure_ascii=False),encoding='utf8')
def edit(n,a,b):
 p=P/n;s=p.read_text(encoding='utf8');assert s.count(a)==1,(n,a[:80]);p.write_text(s.replace(a,b),encoding='utf8')
assert not P.exists();P.mkdir();manifest=read(S/'MANIFEST.json')['files']
for n,h in manifest.items():
 assert sha(S/n)==h;npath=P/n;npath.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(S/n,npath)
plan=read(S/'PROTOCOL.json');oldjob=plan['job_id'];newjob='btc5m-v12g-small300-demand-scale-audit-resume-20260928-v56r1'
out=R/'lan_worker_returns'/oldjob;res=read(out/'RESULT.json');assert res['status']=='STOPPED' and len(res['paths'])==6
assert read(S/'CROSS_MACHINE_VERIFIED.json')['status']=='PASS'
completed={(x['arm'],x['market']) for x in res['paths']}
oldjobs=plan['jobs'];plan['jobs']=[j for j in oldjobs if (j['arm'],j['market']) not in completed];assert len(plan['jobs'])==32
plan.update(job_id=newjob,version='V56R1_AUDIT_ONLY_RESUME32',original_job=oldjob,total_original_paths=38,max_new_native=32)
plan['completed_source_hashes']={str((out/n).relative_to(R/'lan_worker_returns').as_posix()):h for n,h in read(S/'REMOTE_HASHES.json').items()}
save('PROTOCOL.json',plan);save('SOURCE_PARENT.json',dict(parent=S.name,source_hashes={S.name+'/'+n:h for n,h in manifest.items()}))
edit('discover.py',oldjob,newjob)
edit('scale_audit.py',"    assert pol['mode']==job['scale_mode'] and pol['factor']==.2", "    return validate(pol,tr,r,job)\n\ndef validate(pol,tr,r,job):\n    assert pol['mode']==job['scale_mode'] and pol['factor']==.2")
edit('scale_audit.py',"    for x,d in zip(pol['rows'],demand):", "    growth=tr['addition_growth_rows'];hold=tr['growth_hold_rows']\n    assert len(growth)==len(hold)==len(demand)\n    for x,g,h,d in zip(pol['rows'],growth,hold,demand):")
edit('scale_audit.py',"        changed+=int(scaled)\n        # Growth gates modify only strong raw demand. The weak input remains raw.\n        strong=x['side'] or 'UP';weak='DOWN' if strong=='UP' else 'UP'\n        assert abs(d['original_desired'][weak]-x['scaled'][weak])<1e-7", "        changed+=int(x['raw']!=x['scaled'])\n        strong=x['side'] or 'UP';weak='DOWN' if strong=='UP' else 'UP'\n        # Growth keeps its physical anchor across flips; follow each actual seam.\n        assert x['t']==g['t']==h['t']==d['t'] and x['index']==h['index']\n        assert x['scaled']==g['original_desired']\n        assert g['effective_desired']==h['input_desired']\n        assert h['effective_desired']==d['original_desired']\n        fixed=g['strong'];other='DOWN' if fixed=='UP' else 'UP'\n        assert g['effective_desired'][other]==g['original_desired'][other]\n        for s in ('UP','DOWN'):\n            assert 0<=h['effective_desired'][s]<=g['effective_desired'][s]+1e-8<=g['original_desired'][s]+2e-8")
edit('worker.py',"len(plan['markets'])==10 and len(plan['jobs'])==38", "len(plan['markets'])==10 and len(plan['jobs'])==32")
edit('worker.py',"    for j in plan['jobs']:\n", "    for n,h in plan['completed_source_hashes'].items():assert sha(W/'.lan_worker_v1/results'/n)==h,n\n    assert read(P/'RECHECKED6.json')['status']=='PASS'\n    for j in plan['jobs']:\n")
edit('worker.py','next_index=2','next_index=0')
s=(P/'worker.py').read_text(encoding='utf8');a=s.index("        progress('INERT_CONTROL'");b=s.index('        with ThreadPoolExecutor',a)
s=s[:a]+"        progress('REUSE_VERIFIED_CONTROL_SMOKE_AND_FOUR_BASELINES',already_completed=6)\n"+s[b:]
s=s.replace('COMPLETE_V56_38','COMPLETE_V56R1_32').replace('len(completed)==38','len(completed)==32').replace('paths=38','paths=32')
(P/'worker.py').write_text(s,encoding='utf8')
unchanged=[n for n in manifest if n not in ('scale_audit.py','worker.py','discover.py','PROTOCOL.json','SOURCE_PARENT.json','CONTRACT.md')]
assert all(sha(P/n)==manifest[n] for n in unchanged)
save('AUDIT_ONLY_PROOF.json',dict(status='PASS',unchanged_files={n:manifest[n] for n in unchanged},old_manifest_sha=sha(S/'MANIFEST.json'),actor_native_clock_sizing_scale_unchanged=True,reruns=0))
(P/'CONTRACT.md').write_text((S/'CONTRACT.md').read_text(encoding='utf8')+'\n## Audit-only correction and resume\nOriginal six native paths COMPLETE; four failed new audit because it assumed growth strong==current role strong. Existing growth freezes its physical side once anchored. Correct audit follows raw scaled→growth original/effective→hold input/effective→demand original/effective for both sides. Recheck all6 locally with negative corruption tests; preserve original failures. Actor, source scaling, clock/native and experiment parameters byte-identical. Continue only32 unstarted paths; no repeated control/smoke or fit.\n',encoding='utf8')
print(P)
