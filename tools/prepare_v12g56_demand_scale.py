"""Create a new bounded research package; never modify frozen parents."""
import hashlib,json,shutil
from pathlib import Path
R=Path(__file__).resolve().parents[1]/'data/research'
SRC=R/'v12g_passive10_uncapped_20260928_v55'
P=R/'v12g_small300_demand_scale_20260928_v56'
JOB='btc5m-v12g-small300-demand-scale-20260928-v56'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text(encoding='utf8'))
def save(n,x): (P/n).write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf8')
def edit(n,a,b):
    p=P/n;s=p.read_text(encoding='utf8');assert s.count(a)==1,(n,a[:70],s.count(a));p.write_text(s.replace(a,b),encoding='utf8')
assert not P.exists();P.mkdir()
parent=read(SRC/'MANIFEST.json')['files']
for n,h in parent.items():
    assert sha(SRC/n)==h,n
    (P/n).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(SRC/n,P/n)
save('SOURCE_PARENT.json',dict(parent=SRC.name,source_hashes={SRC.name+'/'+n:h for n,h in parent.items()}))
edit('discover.py','btc5m-v12g-passive10-uncapped-20260928-v55',JOB)
for n in ('patches.py','worker.py'):
    p=P/n;p.write_text(p.read_text(encoding='utf8').replace('v12g55_','v12g56_'),encoding='utf8')
edit('sizing.py','    return once(source, \'passive_ticket=15.\', "passive_ticket=__import__(\'sizing\').TICKET")',
     '    source=once(source, \'passive_ticket=15.\', "passive_ticket=__import__(\'sizing\').TICKET")\n    return once(source, \'    source=transform_policy(source)\', "    source=__import__(\'demand_scale\').instrument(source)\\n    source=transform_policy(source)")')
edit('overlay/run_variant.py',"        from sizing import TICKET\n", "        from demand_scale import finish as finish_demand_scale\n        r['v56_demand_scale']=finish_demand_scale(out)\n        from sizing import TICKET\n")
cohort=read(R/'v12g_small300_opening_diagnostic_20260928_v54/NEXT_TEN_COHORT.json')['markets']
markets=[x['market'] for x in cohort]
old=read(SRC/'PROTOCOL.json');large=read(R/'v12g_qualified_work_continuation_20260927_v50/COMPARISON.json')
large_rows={x['market']:x for x in large['rows'] if x['arm']=='V50'}
assert set(markets)<=set(large_rows),set(x['arm'] for x in large['rows'])
base={};large_ref={};reuse={}
for m in markets:
    d=R/large_rows[m]['source_path']
    names=['result.json','clock_trace.json.gz','execution_clock.json','restoration_trace.json.gz','risk_floor_trace.json.gz','qualified_work_trace.json.gz']
    large_ref[str(m)]=dict(local_source=d.relative_to(R).as_posix(),remote_relative=d.relative_to(R/'lan_worker_returns').as_posix(),files={n:sha(d/n) for n in names})
    if str(m) in old['baseline']:
        base[str(m)]=old['baseline'][str(m)];reuse[str(m)]=base[str(m)]
    else:base[str(m)]=large_ref[str(m)]
env=old['jobs'][0]['env_v12']
def job(arm,m):
    scale='OFF' if arm in ('INERT','BASE') else arm
    e=dict(env,V12G_DEMAND_SCALE_MODE=scale,V12G_DEMAND_SCALE='0.2',V12G_GROSS='60' if scale in ('OPEN','BOTH') else '300')
    return dict(arm=arm,market=m,mode='BASE',cap=300.,ticket=10.,scale_mode=scale,env_v12=e)
jobs=[job('INERT',2633749),job('BOTH',2633749)]
jobs += [job('BASE',m) for m in markets if str(m) not in reuse]
jobs += [job(a,m) for a in ('OPEN','POST','BOTH') for m in markets if not(a=='BOTH' and m==2633749)]
assert len(jobs)==38
plan=dict(version='V56_RAW_DEMAND_PHASE_SCALE',job_id=JOB,markets=markets,jobs=jobs,baseline=base,reused_cap300=reuse,large_reference=large_ref,
    groups=dict(ALL10=markets,ORIGINAL_TOP5=markets[:5],ORIGINAL_BOTTOM5=markets[5:],ORIGINAL_NO_FLIP=[x['market'] for x in cohort if not x['actual_flips']],ORIGINAL_ANY_FLIP=[x['market'] for x in cohort if x['actual_flips']]),
    cohort=cohort,parallel_paths=4,max_threads=4,native_threads_per_path=1,max_new_native=38,consumed=True,excluded_censored=2629444,
    factor=.2,factor_reason='Fixed coarse pilot: 300/previous six-market uncapped passive10 mean1436.81 approximately0.209, rounded0.2. Not fitted per market, not optimality claim.',
    dedup='V53 scaled tickets only; V55 removed cap only; V54 read-only. New raw-demand phase scale with matched opening gross threshold; original financial repair floors, active quantities, cap and pending unchanged.')
save('PROTOCOL.json',plan)
edit('worker.py',"len(plan['markets'])==6 and len(plan['jobs'])==7", "len(plan['markets'])==10 and len(plan['jobs'])==38")
edit('worker.py',"    assert run()['status']=='PASS'\n", "    assert run()['status']=='PASS'\n    from test_demand_scale import run as scale_tests\n    assert scale_tests()['status']=='PASS'\n")
edit('worker.py',"raw['v53_sizing']['gross_decision_threshold']==300.", "raw['v53_sizing']['gross_decision_threshold']==float(job['env_v12']['V12G_GROSS'])")
edit('worker.py',"        row['passive_size_verified_orders']=len(passive)", "        from scale_audit import audit as scale_audit\n        row.update(scale_audit(arm,job))\n        row['passive_size_verified_orders']=len(passive)")
for a,b in [('paths=7','paths=38'),('COMPLETE_V55_7','COMPLETE_V56_38'),('len(completed)==7','len(completed)==38'),('MODIFIED_NOCAP_SMOKE','MODIFIED_BOTH_SMOKE')]:
    p=P/'worker.py';p.write_text(p.read_text(encoding='utf8').replace(a,b),encoding='utf8')
(P/'CONTRACT.md').write_text('''# V56: 300 USDT raw-demand phase scale

Bounded 38 native paths: identical OFF control1, missing cap300/passive10 baseline7, OPEN/POST/BOTH10 each; reuse3 cap baselines. Cohort is V54 original V50 top5+bottom5, already consumed and outcome-selected; not representative or fresh generalization. First control and BOTH smoke must pass before four single-thread paths run concurrently. No reruns, fits, live orders, service or collector changes.

Global factor0.2 is a coarse predeclared pilot based on previous average cost, not a fitted optimum. OPEN scales raw gross*share targets while side is None and changes gross decision threshold300 to60. POST scales raw targets only after causal side selection. BOTH does both. Fallback30 seconds, price/flip rules, passive10, active PADD15, active repair quantities, financial capacity, finite repair goal, ledger/pending and cap300 remain unchanged. Raw target is upstream of all growth and demand gates; finite repair floor can exceed the scaled raw target. Existing orders still require terminal receipt to release reservations. Lower demand may legitimately change future orders and maintenance; no claim of unchanged repair fills or direction timing.

Primary: paired winner mean, original top5 earnings and no-FLIP4 retention, original bottom5/tail loss, cost efficiency, first60 payoff and activity. Also signed UP/DOWN, P>L percentage, both-positive/both-negative, actual route/role fills, repair obligation/goal checks, funding clipping, cap and unresolved owners. An inactive or near-flat result is not success. Outcome labels only in offline evaluation. All source, ownership, receipt and clock checks must pass; inherited active_matches_opportunity failures are reported separately. Stop on native/audit/UNKNOWN failure, preserve evidence and diagnose a pinned repair rather than resubmit.
''',encoding='utf8')
print(P)
