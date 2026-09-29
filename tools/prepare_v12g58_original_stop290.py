"""Prepare original-scale STOP290 only; no native or live work on this host."""
import hashlib,json,shutil
from pathlib import Path

R=Path(__file__).resolve().parents[1]/'data/research'
S=R/'v12g_stop290_cancel_all_20260928_v57'
V=R/'v12g_qualified_work_continuation_20260927_v50'
P=R/'v12g_original_scale_stop290_20260928_v58'
read=lambda p:json.loads(p.read_text(encoding='utf8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,x):(P/n).write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf8')
def edit(n,a,b):
 p=P/n;s=p.read_text(encoding='utf8');assert s.count(a)==1,(n,a,s.count(a));p.write_text(s.replace(a,b),encoding='utf8')

assert not P.exists();P.mkdir()
manifest=read(S/'MANIFEST.json')['files']
for n,h in manifest.items():
 assert sha(S/n)==h,n
 if n not in {'PROTOCOL.json','CONTRACT.md','SOURCE_PARENT.json'}:
  (P/n).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(S/n,P/n)
save('SOURCE_PARENT.json',dict(parent=S.name,source_hashes={S.name+'/'+n:h for n,h in manifest.items()}))
jobid='btc5m-v12g-original-scale-stop290-20260928-v58'
edit('discover.py','btc5m-v12g-stop290-cancel-all-20260928-v57',jobid)
for n in ('worker.py','patches.py'):
 p=P/n;p.write_text(p.read_text(encoding='utf8').replace('v12g57_','v12g58_'),encoding='utf8')
edit('worker.py','len(plan[\'markets\'])==10 and len(plan[\'jobs\'])==11','len(plan[\'markets\'])==29 and len(plan[\'jobs\'])==30')
p=P/'worker.py';s=p.read_text(encoding='utf8').replace('COMPLETE_V57_11','COMPLETE_V58_30').replace('len(completed)==11','len(completed)==30').replace('paths=11','paths=30')
s=s.replace('"""One inert control and uncapped passive10 on six consumed worker cases."""','"""Original V50 scale plus STOP290; 29 consumed pairs and one inert control."""')
p.write_text(s,encoding='utf8')
old=read(V/'COMPARISON.json');markets=old['groups']['VALID_PAIRED'];rows={x['market']:x for x in old['rows'] if x['arm']=='V50'}
baseline={}
for m in markets:
 d=R/rows[m]['source_path']
 names=['result.json','clock_trace.json.gz','execution_clock.json','restoration_trace.json.gz','risk_floor_trace.json.gz','EXECUTION.json','qualified_work_trace.json.gz']
 baseline[str(m)]=dict(local_source=d.relative_to(R).as_posix(),remote_relative=d.relative_to(R/'lan_worker_returns').as_posix(),files={n:sha(d/n) for n in names})
env=read(R/baseline['2629327']['local_source']/'EXECUTION.json')['env_v12']
assert 'V12G_MARKET_CAP' not in env and env['V12G_QUALIFIED_CONTINUATION']=='ON'
def job(arm,m):return dict(arm=arm,market=m,mode='BASE',cap=None,ticket=15.,scale_mode='OFF',stop_mode='OFF' if arm=='INERT' else 'ON',env_v12=dict(env,V12G_POSTFLIP_ADD_MODE='BASE',V12G_PASSIVE_TICKET='15.0',V12G_DEMAND_SCALE_MODE='OFF',V12G_DEMAND_SCALE='0.2',V12G_STOP290='OFF' if arm=='INERT' else 'ON'))
jobs=[job('INERT',2629327),job('STOP290',2629327)]+[job('STOP290',m) for m in markets if m!=2629327]
save('PROTOCOL.json',dict(version='V58_ORIGINAL_SCALE_STOP290',job_id=jobid,markets=markets,jobs=jobs,baseline=baseline,groups=old['groups'],parallel_paths=4,max_threads=4,native_threads_per_path=1,max_new_native=30,consumed=True,excluded_censored=2629444,cutoff_elapsed_ms=290000,model_fits=0,live_changes=0,
 dedup='V57 tested only cap300/passive10/POST20 on ten selected markets and had zero nonempty cancellation frames. V58 restores original V50 cap=null/passive15/raw demand OFF and tests the universal cutoff on all29 consumed markets, preserving opening and all pre290 decisions. No new repair rule; no repeat of V52 postFLIP ADD reduction.',
 scope='One bounded original-scale baseline establishment; unknowns stop, no auto repair/rerun. First OFF control full parity; ON prefix then full cohort. Existing V50 results reused.',
 actor_parent_manifest_sha256=sha(S/'MANIFEST.json'),parent_comparison_sha256=sha(V/'COMPARISON.json')))
(P/'CONTRACT.md').write_text('''# V58 original-scale baseline with user-requested STOP290

User explicitly chose original-scale reversal-loss research before further cap300 sizing. Restore V50 economic settings: no capital ceiling, passive15, active PADD15, gross300 original selection threshold, unscaled demand, original qualified repair/continuation. V57 implementation supplies only universal t>=start+290000 cancel-all/no-NEW. Skip all appenders before side effects, retain real end/latency/fees/tape, and pending ownership until canonical terminal receipt. No liquidations, fake cash release, final-winner or Target runtime inputs.

Thirty unique native paths: one OFF control2629327 and ON all29 consumed V50 markets. First smoke2629327 (complex qualified continuation), then at most4 single-thread paths. V50 baseline is reused across original and audit-resume jobs, exact sources pinned. Excluded2629444 remains UNKNOWN; no replacement. Zero fits/live/deployment/service/collector changes. This is not fresh generalization. Exact-name one submit only; fail-stop and preserve evidence, no rerun after transport timeout.

Acceptance: OFF exact complete path; ON exact pre290 prefix; every NEW<290; all cancellable nonterminal owners requested once per frame except existing CANCEL_PENDING; retry later-acknowledged owners; no reservation release on cancel intention. Track actual receipt/exchange timestamps, cancel race fills, all final owners and source consumption. Nonempty cancellation coverage must be reported separately; if no such market occurs, do not claim it passed. No fills at/after actual expiry permitted for this candidate acceptance.

Report original9 noFLIP and20 anyFLIP groups separately (strategy-event labels, not ex-ante oracle), signed branches, P>L, payoff/paid capital, average and worst2/worst5, original profitable examples and tail failures. An average improvement cannot hide worse cases or unbounded funding demands. Carry existing active_matches_opportunity failures explicitly.

Next mechanism work remains finite partial repair independent of old global-entry latch and confirmation-driven re-expansion; this round changes neither. V51/V52 diagnosis is reference, not already-proven counterfactual repair value. All future dispatches require separate frozen scopes; no unbounded research loop.
''',encoding='utf8')
print(P)
