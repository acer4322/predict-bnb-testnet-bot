"""Research-only user-requested 290s cancel-all, on the retained POST candidate."""
import hashlib,json,shutil
from pathlib import Path
R=Path(__file__).resolve().parents[1]/'data/research';S=R/'v12g_small300_demand_scale_audit_resume_20260928_v56r1';P=R/'v12g_stop290_cancel_all_20260928_v57'
read=lambda p:json.loads(p.read_text(encoding='utf8'));sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,x):(P/n).write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf8')
def edit(n,a,b):
 p=P/n;s=p.read_text(encoding='utf8');assert s.count(a)==1,(n,a[:70],s.count(a));p.write_text(s.replace(a,b),encoding='utf8')
assert not P.exists();P.mkdir();manifest=read(S/'MANIFEST.json')['files']
omit={'PROTOCOL.json','CONTRACT.md','SOURCE_PARENT.json','AUDIT_ONLY_PROOF.json','RECHECKED6.json','recheck_six.py','summarize.py'}
for n,h in manifest.items():
 assert sha(S/n)==h,n
 if n not in omit:
  (P/n).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(S/n,P/n)
save('SOURCE_PARENT.json',dict(parent=S.name,source_hashes={S.name+'/'+n:h for n,h in manifest.items()}))
jobid='btc5m-v12g-stop290-cancel-all-20260928-v57'
edit('discover.py','btc5m-v12g-small300-demand-scale-audit-resume-20260928-v56r1',jobid)
for n in ('worker.py','patches.py'):
 p=P/n;p.write_text(p.read_text(encoding='utf8').replace('v12g56_','v12g57_'),encoding='utf8')
edit('sizing.py','    source=once(source, \'passive_ticket=15.\',',
     '    source=once(source, \'    source=bridge.instrument(source,replace)\', "    source=bridge.instrument(source,replace)\\n    source=__import__(\'hard_stop\').instrument(source)")\n    source=once(source, \'passive_ticket=15.\',')
edit('overlay/run_variant.py',"        from demand_scale import finish as finish_demand_scale", "        from hard_stop import finish as finish_hard_stop\n        r['v57_stop290']=finish_hard_stop(out)\n        from demand_scale import finish as finish_demand_scale")
old=read(S/'PROTOCOL.json');comparison=read(S/'COMPARISON.json');markets=old['markets'];rows={x['market']:x for x in comparison['rows'] if x['arm']=='POST'}
baseline={}
for m in markets:
 d=R/rows[m]['source'];names=['result.json','clock_trace.json.gz','execution_clock.json','restoration_trace.json.gz','risk_floor_trace.json.gz','EXECUTION.json','governor_trace.json.gz','demand_scale_trace.json.gz','qualified_work_trace.json.gz']
 baseline[str(m)]=dict(local_source=d.relative_to(R).as_posix(),remote_relative=d.relative_to(R/'lan_worker_returns').as_posix(),files={n:sha(d/n) for n in names})
env=read(R/baseline[str(markets[0])]['local_source']/'EXECUTION.json')['env_v12'];assert env['V12G_DEMAND_SCALE_MODE']=='POST'
def job(arm,m):return dict(arm=arm,market=m,mode='BASE',cap=300.,ticket=10.,scale_mode='POST',stop_mode='OFF' if arm=='INERT' else 'ON',env_v12=dict(env,V12G_STOP290='OFF' if arm=='INERT' else 'ON'))
jobs=[job('INERT',2632221),job('STOP290',2632221)]+[job('STOP290',m) for m in markets if m!=2632221]
save('PROTOCOL.json',dict(version='V57_STOP290_CANCEL_ALL',job_id=jobid,markets=markets,jobs=jobs,baseline=baseline,groups=old['groups'],cohort=old['cohort'],parallel_paths=4,max_threads=4,native_threads_per_path=1,max_new_native=11,consumed=True,excluded_censored=2629444,
 cutoff_elapsed_ms=290000,scope='All NEW, all routes and ADD/repair roles; request cancellation of every cancellable nonterminal owner on each frame at/after cutoff; pending not released before terminal receipt.',
 dedup='V56 flagged expiry exchange fills. Existing tail gate only directional PASSIVE in final third; PADD has own 10s stop but active repairs bypass. New universal hard boundary explicitly requested by user; no change to expiry or engine/tape/latency.',
 actor_parent_manifest_sha256=sha(S/'MANIFEST.json'),parent_comparison_sha256=sha(S/'COMPARISON.json')))
edit('worker.py',"len(plan['markets'])==10 and len(plan['jobs'])==32", "len(plan['markets'])==10 and len(plan['jobs'])==11")
edit('worker.py',"    for n,h in plan['completed_source_hashes'].items():assert sha(W/'.lan_worker_v1/results'/n)==h,n\n    assert read(P/'RECHECKED6.json')['status']=='PASS'\n",'')
edit('worker.py',"    assert scale_tests()['status']=='PASS'\n", "    assert scale_tests()['status']=='PASS'\n    from test_hard_stop import run as stop_tests\n    assert stop_tests()['status']=='PASS'\n")
edit('worker.py',"        row.update(scale_audit(arm,job))", "        row.update(scale_audit(arm,job))\n        from stop_audit import audit as stop_audit\n        row.update(stop_audit(arm,baseline,job))")
edit('worker.py','next_index=0','next_index=2')
edit('worker.py',"        progress('REUSE_VERIFIED_CONTROL_SMOKE_AND_FOUR_BASELINES',already_completed=6)", "        progress('INERT_CONTROL',market=plan['jobs'][0]['market']);started.add(0)\n        control=path_job(plan['jobs'][0],plan);completed[0]=control;result['paths']=[control];save(OUT/'PARTIAL.json',result)\n        if control['status']!='PASS':blocked='INERT_CONTROL_FAILED'\n        if not blocked:\n            progress('STOP290_SMOKE',market=plan['jobs'][1]['market']);started.add(1)\n            smoke=path_job(plan['jobs'][1],plan);completed[1]=smoke;result['paths']=[completed[i] for i in sorted(completed)];save(OUT/'PARTIAL.json',result)\n            if smoke['status']!='PASS':blocked='STOP290_SMOKE_FAILED'")
p=P/'worker.py';s=p.read_text(encoding='utf8').replace('COMPLETE_V56R1_32','COMPLETE_V57_11').replace('len(completed)==32','len(completed)==11').replace('paths=32','paths=11');p.write_text(s,encoding='utf8')
(P/'CONTRACT.md').write_text('''# V57 user-requested stop290/cancel-all

The user explicitly requests no further orders after elapsed290 seconds and cancellation of all resting orders. Apply t >= start+290000ms (or actual market end if earlier), across all ACTIVE/PASSIVE, ADD/repair mechanisms. On the first observed strategy frame at/after cutoff and every subsequent frame, request CANCEL for every cancellable nonterminal owner not already CANCEL_PENDING. Retry previously unacknowledged/noncancellable owners when they become cancellable. No synthetic terminal status, cash release, forced fill, position liquidation, or end-clock change. Already sent orders may fill before canonical cancellation receipt; account honestly.

Research-only successor of V56 POST20, cap300/passive10/PADD15 unchanged. Existing expiry branch is reached early; all four envelope service appenders are skipped before invocation so no new reservation or hidden birth occurs. Final plan guard requires cancel-only and complete cancellable coverage. Atomic receipts/demand owner observation continue; unfinished goals remain real obligations/withdrawals, not invented completed repairs. OFF is uninstrumented full-path control.

11 unique native paths: OFF control2632221, ON smoke2632221, then other9 original ten markets at max4 single-thread paths. Reuse all10 prior POST; no extra BASE reruns. Freeze inputs before stage, load-only first, fail-stop on UNKNOWN/native/audit problems. No fits, live orders, deployment, services or collectors changed. Causal public clock/OUR portfolio only; winners read only after replay for evaluation.

Validate boundary289999/290000/290001, ACTIVE and PASSIVE appenders, pending/partial/UNKNOWN/cancel-pending lifecycle, and later cancellability. Prefix plans/states/actions before290 must match baseline exactly. Audit all NEW births >=290, cancel coverage, reservation retention, cancellation-race fills, actual exchange fills >=300, final owners, signed UP/DOWN, P>L and same-market profit/loss change. First frame may follow290 because the frozen research engine is event-driven; report exact cutoff reaction lag. No deadline synthetic frame/tape change. This is10 consumed outcome-selected BTC/zero-fee diagnostics, not new generalization or live acceptance.
''',encoding='utf8')
print(P)
