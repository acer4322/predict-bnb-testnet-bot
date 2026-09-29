"""Immutable continuation package. Only code transforms/hash/compile on host."""
from pathlib import Path
import hashlib,json,py_compile,shutil
ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'.lan_worker_v1/minimal_student_open_funding_train_logfix_20260911_v2'
OUT=ROOT/'.lan_worker_v1/open_funding_recovery_train_20260911_v3'
R=ROOT/'data/research/r4_v0/p0_provenance_v1'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    assert not OUT.exists(),'immutable package exists'
    old=json.loads((OLD/'MANIFEST.json').read_text(encoding='utf-8'))
    source=OLD/'run_minimal_student_open_funding_train_v1.py';assert sha(source)==old['files'][source.name]['sha256']
    s=source.read_text(encoding='utf-8')
    def edit(a,b):
        nonlocal s
        assert s.count(a)==1,(a[:100],s.count(a));s=s.replace(a,b)
    edit(".tmp/minimal_student_open_funding_train_logfix_20260911_v2'",".tmp/open_funding_recovery_train_20260911_v3'")
    edit("'--child'],600)","'--child'],1200)")
    edit("version='MINIMAL_STUDENT_OPEN_FUNDING_TRAIN_V1'","version='OPEN_FUNDING_RECOVERY_TRAIN_V3'")
    edit("self.terminal_logged=set()","self.terminal_logged=set();self.prefix_hashes={k:hashlib.sha256() for k in ('native_action','canonical_receipt','own_state')};self.prefix_counts={k:0 for k in self.prefix_hashes}")
    edit("self.f.write(text)","""self.f.write(text)
        if kind in self.prefix_hashes:
            self.prefix_hashes[kind].update((json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)+'\\n').encode())
            self.prefix_counts[kind]+=1""")
    edit("self.plans+=1;fr=event['input_frame'];actions=event['operations']", """self.plans+=1;fr=event['input_frame'];actions=event['operations']
        if self.plans%250==0:print(json.dumps(dict(stage='policy_heartbeat',trace=self.path.name,frames=self.plans)),flush=True)""")
    edit("sys.path.insert(0,str(ROOT));os.chdir(ROOT)","""runtime_path=ROOT/'tools/open_funding_recovery_runtime_v3.py'
        shutil.copy2(BUNDLE/'open_funding_recovery_runtime_v3.py',runtime_path)
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools.open_funding_recovery_runtime_v3 import self_tests as recovery_tests,FastOpenFundingLedger,make_recovered_student,source_signature,audit_receipt_support,scalar_asdict
        result['recovery_tests']=recovery_tests()
        print(json.dumps(dict(stage='recovery_unit_tests_pass',tests=result['recovery_tests']['passed'])),flush=True)""")
    edit("Student=make_training_class(minimal.MinimalPairRoleSim,make_student_class(minimal.MinimalPairRoleSim))", """Student=make_recovered_student(minimal.MinimalPairRoleSim,make_student_class(minimal.MinimalPairRoleSim))
        OpenFundingLedger=FastOpenFundingLedger""")
    edit("profile=WorldProfile()","profile=WorldProfile(max_live_owners=4096)")
    edit("def run_case(theta,variant,mid,s,split):","def run_case(theta,variant,mid,s,split,resource_limit=4096):")
    edit("assert result['native_attempts']<=10","assert result['native_attempts']<=11")
    edit("ledger=OpenFundingLedger(profile)","case_profile=WorldProfile(max_live_owners=resource_limit)\n            ledger=OpenFundingLedger(case_profile)")
    edit("sim=Student(ROOT/f'tapes/{mid}.json.xz',profile.max_live_owners,False,", "sim=Student(ROOT/f'tapes/{mid}.json.xz',case_profile.max_live_owners,False,")
    edit("sim.bt=AuditBT(sim.bt,tr)\n            sim.run_whole(base)", """sim.bt=AuditBT(sim.bt,tr)
            assert len(sim.payload['updates'])<2048,'source observation bound exceeds proven nonbinding engineering capacity'
            sim.run_whole(base)
            prefix=dict(counts=dict(tr.prefix_counts),sha256={k:v.hexdigest() for k,v in tr.prefix_hashes.items()})
            if split=='GOLDEN':
                old_trace=Path('C:/BTC5M-worker/.lan_worker_v1/results/minimal-student-open-funding-train-logfix-20260911-v2/TRAIN_INITIAL_2022527.jsonl.gz')
                assert sha(old_trace)=='194e817ab1d8d2332301edbf5f6bbe98cfc778facad9330cbd3b19bb04e1b6dc'
                expected=source_signature(old_trace)
                assert prefix==expected,'source-prefix physical actions/receipts/own state changed'
                result['golden_source_prefix_exact']=True;save()
                print(json.dumps(dict(stage='golden_source_prefix_exact',counts=prefix['counts'])),flush=True)
            drain=sim.drain_queued_responses(base)
            result['latest_terminal_drain']=drain
            if any(c.state!='TERMINAL' for c in sim.gateway.ledger.carriers.values()):
                raise RuntimeError('UNRESOLVED_AFTER_NATIVE_ONLY_DRAIN_NO_FABRICATED_TERMINAL')""")
    edit("row=dict(variant=variant,market_id=mid,split=split,theta=list(theta),loss=loss,", """row=dict(variant=variant,market_id=mid,split=split,theta=list(theta),loss=loss,
                source_prefix=prefix,terminal_drain=drain,resource_limit=resource_limit,""")
    edit("tr=None;sim.close();sim=None", """tr=None
            row['receipt_support']=audit_receipt_support(out/row['trace']['path'],{k:scalar_asdict(c) for k,c in actual.carriers.items()})
            assert row['receipt_support']['canonical_zero_fill_orders']==row['zero_fill_orders']
            if split!='GOLDEN':assert row['resource_censor_events']==0,'engineering ceiling unexpectedly censored this policy'
            sim.close();sim=None""")
    edit("rng=random.Random(20260911);direction=", """golden=run_case(INITIAL,'RECOVERY_GOLDEN',2022527,train_sources[2022527],'GOLDEN',resource_limit=32)
        result['golden_terminal_closed']=golden['unresolved_owners']==0
        result['old_count_discrepancy_resolved']=golden['receipt_support']['numeric_residual_only_orders']==57
        save()
        rng=random.Random(20260911);direction=""")
    edit("verdict='OPEN_FUNDING_WHOLE_POLICY_TRAINING_AND_FROZEN_CHECK_COMPLETED'", "verdict='RECOVERED_OPEN_FUNDING_TRAINING_AND_FROZEN_CHECK_COMPLETED'")
    edit("for file in ('minimal_student_joint_policy_train_v1.py','minimal_student_open_funding_v1.py','PREREG.md'):","for file in ('minimal_student_joint_policy_train_v1.py','minimal_student_open_funding_v1.py','open_funding_recovery_runtime_v3.py','PREREG.md'):")
    edit("'Resource32 is engineering capacity; any binding event is reported and prevents unrestricted-world promotion.'", "'Resource4096 is a proven nonbinding bound for max2new per less2048observations in this fixed policy/cohort; no order-censor events permitted.',\n                'Golden32 source prefix exactly matches previous actions/receipts/balances before queued-response drain.',\n                'Final EOF observation may be a conservative query-time upper bound, not exact native terminal time.'")
    runner=ROOT/'tools/run_open_funding_recovery_train_v3_20260911.py'
    assert not runner.exists();runner.write_text(s,encoding='utf-8');py_compile.compile(str(runner),doraise=True)
    helper=ROOT/'tools/open_funding_recovery_runtime_v3.py';py_compile.compile(str(helper),doraise=True)
    files={
        runner.name:runner,'open_funding_recovery_runtime_v3.py':helper,
        'PREREG.md':R/'OPEN_FUNDING_RECOVERY_TRAIN_PREREG_V3_20260911.md'}
    for name in ('minimal_student_joint_policy_train_v1.py','minimal_student_open_funding_v1.py','input_2022527.json.gz','input_2022538.json.gz','input_2022602.json.gz'):
        p=OLD/name;assert sha(p)==old['files'][name]['sha256'];files[name]=p
    OUT.mkdir(parents=True)
    for rel,p in files.items():assert p.stat().st_size<1024**2;shutil.copy2(p,OUT/rel)
    metadata={rel:dict(bytes=(OUT/rel).stat().st_size,sha256=sha(OUT/rel)) for rel in files}
    m=dict(old,version='OPEN_FUNDING_RECOVERY_TRAIN_V3',files=metadata,
        max_native_runs=11,capital_cap=None,world_resource_limit=4096,policy_changed=False,
        source_prefix_golden_required=True,terminal_response_only_drain=True,
        old_zero_count_claim='920cumulative_zero =863no_receipt+57numeric_nonadvancing',
        prior_manifest_sha256=sha(OLD/'MANIFEST.json'))
    (OUT/'MANIFEST.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=OUT.relative_to(ROOT).as_posix(),manifest_sha256=sha(OUT/'MANIFEST.json'),
        helper_sha256=sha(helper),actor_sha256=sha(OUT/'minimal_student_open_funding_v1.py'),
        bytes=sum(x['bytes'] for x in metadata.values()),max_new_native_runs=11,capital_cap=None)))


if __name__=='__main__':main()
