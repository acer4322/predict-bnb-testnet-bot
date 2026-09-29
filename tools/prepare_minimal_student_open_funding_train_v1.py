"""Create a separate nonbinding-funding research runner, leaving V1 frozen.
Host work is bounded source transformation, compile, hash and file copy only.
"""
from pathlib import Path
import ast
import hashlib
import json
import py_compile
import shutil

ROOT=Path(__file__).resolve().parents[1]
PREV=ROOT/'.lan_worker_v1/minimal_student_whole_episode_train_20260911_v1'
OUT=ROOT/'.lan_worker_v1/minimal_student_open_funding_train_20260911_v1'
R=ROOT/'data/research/r4_v0/p0_provenance_v1'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    assert not OUT.exists(),'immutable package exists'
    prior=json.loads((PREV/'MANIFEST.json').read_text(encoding='utf-8'))
    old_runner=PREV/'run_minimal_student_whole_episode_train_v1.py'
    assert sha(old_runner)==prior['files'][old_runner.name]['sha256']
    text=old_runner.read_text(encoding='utf-8')
    nodes=[n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name in ('vec','loss_from_trajectories')]
    lines=text.splitlines(keepends=True)
    for n in sorted(nodes,key=lambda n:n.lineno,reverse=True):
        repl=[] if n.name=='vec' else ['def loss_from_trajectories(states,source,terminal,qref):\n',
            '    from tools.minimal_student_open_funding_v1 import path_loss\n',
            '    return path_loss(states,source,terminal,qref)\n']
        lines[n.lineno-1:n.end_lineno]=repl
    text=''.join(lines)
    def change(old,new):
        nonlocal text
        if text.count(old)!=1:raise ValueError('unmatched exact edit '+old[:90])
        text=text.replace(old,new)
    change(".tmp/minimal_student_whole_episode_train_20260911_v1'",".tmp/minimal_student_open_funding_train_20260911_v1'")
    change("version='MINIMAL_STUDENT_WHOLE_EPISODE_TRAIN_V1'","version='MINIMAL_STUDENT_OPEN_FUNDING_TRAIN_V1'")
    change("'whole-episode-train'","'open-funding-episode-train'")
    change("self.new_plan_count=0;self.cancel_plan_count=0","self.new_plan_count=0;self.cancel_plan_count=0;self.peak_cash_requirement=0.")
    change("s=dict(t=int(t),inv=dict(sim.inv),cost=float(sim.cost));self.states.append(s)",
        "funding=sim.gateway.ledger.funding_demand()\n        self.peak_cash_requirement=max(self.peak_cash_requirement,funding['current_cash_requirement'])\n        s=dict(t=int(t),inv=dict(sim.inv),cost=float(sim.cost),funding_demand=funding);self.states.append(s)")
    change("self.plans+=1;fr=event['input_frame'];actions=event['operations']",
        "self.plans+=1;fr=event['input_frame'];actions=event['operations']\n        accounts=event['own_after_plan']['accounts']\n        self.peak_cash_requirement=max(self.peak_cash_requirement,sum(a['spent']+a['reserved_cash'] for a in accounts.values()))")
    change("cancel_action_plans=self.cancel_plan_count)","cancel_action_plans=self.cancel_plan_count,peak_cash_requirement=self.peak_cash_requirement,capital_cap=None)")
    change("shutil.copy2(BUNDLE/'minimal_student_joint_policy_train_v1.py',dest)",
        "shutil.copy2(BUNDLE/'minimal_student_joint_policy_train_v1.py',dest)\n        dest=ROOT/'tools/minimal_student_open_funding_v1.py'\n        shutil.copy2(BUNDLE/'minimal_student_open_funding_v1.py',dest)")
    change("sys.path.insert(0,str(ROOT));os.chdir(ROOT)",
        "sys.path.insert(0,str(ROOT));os.chdir(ROOT)\n        from tools.minimal_student_open_funding_v1 import self_tests\n        result['unit_tests']=self_tests()\n        save();print(json.dumps(dict(stage='funding_unit_tests_pass',tests=result['unit_tests']['passed'])),flush=True)")
    change("from tools.minimal_student_training_world_v2 import WorldProfile,TrainingGrantLedger,make_training_class",
        "from tools.minimal_student_training_world_v2 import make_training_class\n        from tools.minimal_student_open_funding_v1 import OpenFundingProfile as WorldProfile, OpenFundingLedger")
    change("from tools.minimal_student_joint_policy_train_v1 import JointWholePolicy,INITIAL,PARAMETER_NAMES",
        "from tools.minimal_student_open_funding_v1 import OpenFundingWholePolicy as JointWholePolicy,initial_parameters,training_reference,PARAMETER_NAMES")
    change("for mid in (2022527,2022538):train_sources[mid]=source(mid)",
        "for mid in (2022527,2022538):train_sources[mid]=source(mid)\n        qref=training_reference(train_sources);INITIAL=initial_parameters(qref)\n        result.update(fixed_train_share_unit=qref,capital_cap=None,funding_mode='VIRTUAL_NONBINDING_RESEARCH',utilization_reward_present=False)\n        save()")
    change("ledger=TrainingGrantLedger(100.,profile)","ledger=OpenFundingLedger(profile)")
    change("Grant(pid,'TRAIN_FIXTURE',side,0.,110.,50.,'FIXED_INITIAL_CAP_NOT_TARGET_BANKROLL')",
        "Grant(pid,'LEARNING_OWNERS',side,0.,0.,0.,'USER_AUTHORIZED_VIRTUAL_NONBINDING_FUNDING')")
    change("loss=loss_from_trajectories(tr.states,s,terminal)",
        "loss=loss_from_trajectories(tr.states,s,terminal,qref)")
    change("unresolved_owners=sum(c.state!='TERMINAL' for c in owners),pending_cash=pending,",
        "unresolved_owners=sum(c.state!='TERMINAL' for c in owners),pending_cash=pending,\n                capital_cap=None,final_funding_demand=actual.funding_demand(),\n                resource_censor_events=sum(v for k,v in producer.declines.items() if 'RESOURCE_CENSOR' in k),")
    change("updated=[max(-6.,min(6.,x-.25*deriv*d)) for x,d in zip(INITIAL,direction)]",
        "updated=[x-.25*deriv*d for x,d in zip(INITIAL,direction)]")
    change("version='WHOLE_EPISODE_PASSIVE_POLICY_V1'","version='OPEN_FUNDING_WHOLE_POLICY_V1'")
    change("core_sha256=sha(dest),objective='OBSERVED_WHOLE_PATH_VECTOR_MSE_PLUS_DOWNSIDE_AND_PENDING'",
        "core_sha256=sha(dest),fixed_train_share_unit=qref,capital_cap=None,objective='UNCAPPED_OBSERVED_SHARES_AND_PAYOFF_BRANCH_PATH_MSE'")
    change("verdict='WHOLE_EPISODE_POLICY_TRAINING_AND_FROZEN_CHECK_COMPLETED'",
        "verdict='OPEN_FUNDING_WHOLE_POLICY_TRAINING_AND_FROZEN_CHECK_COMPLETED'")
    change("for file in ('minimal_student_joint_policy_train_v1.py','PREREG.md'):",
        "for file in ('minimal_student_joint_policy_train_v1.py','minimal_student_open_funding_v1.py','PREREG.md'):")
    change("'Execution queue/latency/fee/world assumptions unchanged; no live transfer claim.'",
        "'Execution queue/latency/fee assumptions unchanged; financial funding is nonbinding simulation, not live feasibility.',\n                'No numeric comparability with the old capped100 score. Training reference fixes units, not a ceiling.',\n                'Resource32 is engineering capacity; any binding event is reported and prevents unrestricted-world promotion.'")
    # Guard the new runner against old budget/reward remnants before packaging.
    assert 'TrainingGrantLedger(100.' not in text and '110.,50.' not in text
    assert 'min(cost/100' not in text and 'max(-6.,min(6.' not in text
    runner=ROOT/'tools/run_minimal_student_open_funding_train_v1.py'
    assert not runner.exists();runner.write_text(text,encoding='utf-8')
    py_compile.compile(str(runner),doraise=True)
    core=ROOT/'tools/minimal_student_open_funding_v1.py';py_compile.compile(str(core),doraise=True)
    files={'run_minimal_student_open_funding_train_v1.py':runner,
        'minimal_student_open_funding_v1.py':core,
        'minimal_student_joint_policy_train_v1.py':PREV/'minimal_student_joint_policy_train_v1.py',
        'PREREG.md':R/'MINIMAL_STUDENT_OPEN_FUNDING_PREREG_V1_20260911.md'}
    for mid in (2022527,2022538,2022602):
        n=f'input_{mid}.json.gz';p=PREV/n;assert sha(p)==prior['files'][n]['sha256'];files[n]=p
    OUT.mkdir(parents=True)
    for rel,p in files.items():
        assert p.stat().st_size<1024**2;shutil.copy2(p,OUT/rel)
    metadata={rel:dict(bytes=(OUT/rel).stat().st_size,sha256=sha(OUT/rel)) for rel in files}
    m=dict(prior,version='MINIMAL_STUDENT_OPEN_FUNDING_TRAIN_V1',files=metadata,
        total_test_capital_unchanged=None,capital_cap=None,funding_mode='VIRTUAL_NONBINDING_RESEARCH',
        parent_policy_source_sha256=sha(old_runner),max_threads=4,max_native_runs=10,live_changes=0)
    (OUT/'MANIFEST.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=OUT.relative_to(ROOT).as_posix(),manifest_sha256=sha(OUT/'MANIFEST.json'),
        runner_path=runner.relative_to(ROOT).as_posix(),runner_sha256=sha(runner),core_sha256=sha(core),
        bytes=sum(v['bytes'] for v in metadata.values()),capital_cap=None,syntax_checked=True)))


if __name__=='__main__':main()
