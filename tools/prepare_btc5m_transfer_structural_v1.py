"""Freeze V32 without fitting for one port parity and four matched structural pairs."""
import ast
import gzip
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import shutil
import sys

import btc5m_transfer_roles_v1 as binding

ROOT = Path(__file__).resolve().parents[1]
R = ROOT/'data/research'
PARENT = ROOT/'.lan_worker_v1/fixed15_addition_growth_2026085_20260913_v1'
INPUT = ROOT/'.lan_worker_v1/core_loop_transfer_ab_inputs_20260913_v1'
SOURCE = ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1'
PACKAGE = ROOT/'.lan_worker_v1/transfer_structural_v32_20260913_v1'
STEM = 'BTC5M_TRANSFER_STRUCTURAL_V32_V1_20260913'
MARKETS = (2023438,2026817,2028352,2029246)
PARITY = 'fixed15-core-loop-2026085-transfer-parity-20260913-v1'


def read(p):
    return json.loads(gzip.decompress(p.read_bytes()) if p.suffix == '.gz' else p.read_text(encoding='utf-8-sig'))


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(tag, value): (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def once(s,a,b):
    assert s.count(a)==1,(a,s.count(a))
    return s.replace(a,b,1)


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def runner_source():
    s=(PARENT/'money_runner.py').read_text(encoding='utf-8')
    s=once(s,"MODES = ('ORACLE_UP', 'ORACLE_DOWN')", "from roles_runtime import roles, transform_policy\nMODES = ('ORACLE_UP', 'ORACLE_DOWN', 'NO_DIRECTION')")
    a=s.index('    def apply(self, legacy, own_net, frame):');b=s.index('\n    def capture(',a)
    s=s[:a]+'''    def apply(self, legacy, own_net, frame):
        self.sign = 1 if roles.side == 'UP' else -1 if roles.side == 'DOWN' else 0
        self.birth = roles.birth
        inv = frame['own_view']['inv']
        if self.held_amplitude is None and abs(inv['UP']-inv['DOWN']) > 1e-8:
            self.held_amplitude = abs(legacy)
            self.amplitude_birth = dict(t=int(frame['t']), index=int(frame['index']), inv=dict(inv),
                amplitude=self.held_amplitude, provenance='FIRST_CONFIRMED_OWN_NET')
        amplitude = abs(legacy) if self.held_amplitude is None else self.held_amplitude
        applied = amplitude if roles.side is not None else 0.
        self.amplitude_rows.append(dict(t=int(frame['t']), index=int(frame['index']), inv=dict(inv),
            current_amplitude=abs(legacy), held_amplitude=self.held_amplitude,
            applied_exposure=applied, amplitude_birth=self.amplitude_birth))
        return applied
''' + s[b:]
    a=s.index('def self_test():');b=s.index('\n\ndef main():',a)
    s=s[:a]+'''def self_test():
    from roles_runtime import self_test as role_test
    role_test()
''' + s[b:]
    s=once(s,"    ap = argparse.ArgumentParser();", "    ap = argparse.ArgumentParser(); ap.add_argument('--market-id', type=int, default=2026085);")
    s=once(s,"    assert args.mode=='ORACLE_UP' and args.repair_route=='NONE'", "    assert args.mode in MODES and args.repair_route=='NONE'")
    marker="    compile(source, str(package/'frozen_runner.py'), 'exec')"
    extra='''    # Pin existing numeric training reference; no test Target source is loaded in this process.
    begin=source.index("  old=json.loads((Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_training_rules_v2_clockfix_20260910')")
    end=source.index("  sys.path.insert(0,str(root));",begin)
    source=source[:begin]+"  public_path=Path(__file__).parent/'inputs'/f'public_{a.market}.json.gz'\\n  source=json.loads(gzip.decompress(public_path.read_bytes()))\\n  assert set(source)=={'market','books','public','tape','actor_input_contract'}\\n  run_window=[source['market']['window_start_ms'],source['market']['window_end_ms']]\\n  run_window_sha=_WINDOW_SOURCE_SHA\\n  tpfile=Path(__file__).parent/'inputs'/'tapes'/f'{a.market}.json.xz'\\n  (root/'tapes').mkdir(parents=True,exist_ok=True)\\n  shutil.copy2(tpfile,root/'tapes'/f'{a.market}.json.xz')\\n"+source[end:]
    begin=source.index('  train_sources=');end=source.index('  class Policy:',begin)
    source=source[:begin]+"  qref=_FIXED_QREF;theta=list(_FIXED_THETA);start,end=run_window;tp=None\\n"+source[end:]
    source=replace(source,'    self.calls+=1;ops=[];', '    roles.observe(f)\\n    self.calls+=1;ops=[];')
    source=replace(source,'    x=stable_features(f)','    x=roles.features(stable_features(f))')
    source=replace(source,"pl=path_loss(tr.states,source,terminal,qref);op=helper.own_profile(tr.states,start,end);score=helper.structural_score(pl['path_mse'],tp,op)",
        "pl=dict(path_mse=None,coordinate_mse=None);op=helper.own_profile(tr.states,start,end);score={}")
    source=replace(source,'target_runtime_access=True,target_scoring_only=False',
        "target_runtime_access=False,target_scoring_only=False")
    source=replace(source,"oracle=True,target_direction_input=True", "oracle=(_MODE!='NO_DIRECTION'),target_direction_input=(_MODE!='NO_DIRECTION')")
    source=replace(source,"lookahead_condition='TARGET_FINAL_OBSERVED_NET_SIDE'", "lookahead_condition=('TARGET_FINAL_OBSERVED_NET_SIDE' if _MODE!='NO_DIRECTION' else None)")
    source=transform_policy(source)
    roles.configure('NO_DIRECTION' if args.mode=='NO_DIRECTION' else 'KNOWN_FINAL_DIRECTION',
                    None if args.mode=='NO_DIRECTION' else ('UP' if args.mode=='ORACLE_UP' else 'DOWN'))
'''
    s=once(s,marker,extra+marker)
    s=once(s,"namespace = dict(__name__='intent_frozen',", "namespace = dict(roles=roles, _FIXED_QREF=manifest['fixed_qref'], _FIXED_THETA=manifest['theta'], _WINDOW_SOURCE_SHA=manifest['window_source_sha256'], __name__='intent_frozen',")
    s=once(s,"payload = dict(addition_growth_rows=", "payload = dict(direction_rows=roles.rows, addition_growth_rows=")
    s=once(s,"target_direction_input=True, oracle=True, runtime_eligible=False", "target_direction_input=(args.mode!='NO_DIRECTION'), oracle=(args.mode!='NO_DIRECTION'), runtime_eligible=False")
    s=once(s,"first_direction_birth=producer.intent.birth, amplitude_mode=", "direction_mode=roles.mode, selected_direction=roles.side, role_semantics='Historical UP-named scalar metrics mean strong; side dictionaries and receipts remain physical', first_direction_birth=producer.intent.birth, amplitude_mode=")
    s=once(s,"'--market', '2026085'", "'--market', str(args.market_id)")
    s=once(s,"limitation='Future-derived direction bit only. Not runtime eligible. Passive/Active branch uses own prefix checkpoint, not Target timing.'", "limitation='Structural consumed-market transfer. Known arm has final Target direction only; no-direction uses first confirmed OWN net. No test Target path in actor process.'")
    return s


def compiled(package,mode='ORACLE_UP'):
    sys.path.insert(0,str(package))
    mod=load('structural_runner_compile',package/'money_runner.py')
    text=inspect.getsource(mod.main);stop="    assert socket.gethostname().upper() == 'DESKTOP-JIERAGF', 'worker only'"
    prefix=text[:text.index(stop)]+'    return source\n'
    ns=dict(mod.__dict__);exec(compile(prefix,'NO_NATIVE_COMPILE_PREFIX','exec'),ns)
    old=sys.argv
    try:
        sys.argv=['component','--mode',mode,'--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE']
        # Existing module tests are UP reference tests; reset before each compilation.
        sys.modules['roles_runtime'].roles.configure('KNOWN_FINAL_DIRECTION','UP')
        return ns['main']()
    finally:sys.argv=old


def main():
    assert not PACKAGE.exists(), 'Existing package must not be rebuilt'
    parent=read(PARENT/'manifest.json');assert all(sha(PARENT/n)==h for n,h in parent['files'].items())
    inputs=read(INPUT/'MANIFEST.json');assert all(sha(INPUT/n)==h for n,h in inputs['files'].items())
    result=read(R/'lan_worker_returns/fixed15-core-loop-2026085-addition-growth-20260913-v1/result.json')
    old=read(SOURCE/'MANIFEST.json')
    # This user turn explicitly moves structural replay before further late-stage tuning.
    dump('PREREGISTERED',dict(status='AUTHORIZED_STRUCTURAL_TRANSFER',candidate='V32_UNCHANGED_ECONOMIC_RULES',
        user_scope='Replay now; focus on structural collapse, not training graduation. Supersedes the previous wait-for-late-adjustment sequencing only.',
        dedup='V32 complete consumed 2026085 retained. Earlier transfer runs used different managers. New test is frozen V32 role-symmetric 4-market direction-conditioned paired replay; one separate original-UP port parity is an engineering bridge check.',
        market_selection=list(MARKETS),max_native_jobs=9,parity_jobs=1,paired_jobs=8,model_fits=0,
        failure_policy='Stop on execution/accounting/port-parity failure, preserve prefix and UNKNOWN fields. Economic weakness does not stop the fixed cohort.',
        bootstrap='Before the first nonzero confirmed OWN net, NO_DIRECTION uses zero directional amplitude and original physical tie/order rules. Pending/owner identities are never relabelled at selection.',
        structural_criteria=['All book frames consumed and terminal cleanup accounted','Native and raw/canonical accounting close','Every NEW obeys same15/variableActive/notional and own-cross rules','Both sides receive fills and repair/addition responses occur','Retain pending until actual canonical terminal','Report economic/path failure separately from engine integrity'],
        target_input='Known direction bit only. NO_DIRECTION contains no Target label; Target path scoring stays outside worker actor process.',
        parity='Original-UP port: exact plans, states, native actions, owner economic flows against collected V32; source hashes differ intentionally.'))
    PACKAGE.mkdir();(PACKAGE/'inputs/tapes').mkdir(parents=True)
    rewrite={'active_opportunity.py','cash_budget.py','commitment_base.py','commitment_repair.py','coordination.py',
        'demand_gate.py','isolated_gate.py','maintenance_scope.py','money_gate.py','parallel_gate.py','persistent_gate.py','pulse_gate.py'}
    for name in parent['files']:
        s=(PARENT/name).read_text(encoding='utf-8')
        if name=='money_runner.py':s=runner_source()
        elif name in rewrite:
            if name in ('active_opportunity.py','coordination.py'):
                s=once(s,"frame['book']['bids']",'roles.weak_bid_book(frame)')
            s=binding.transform_module(s)
        elif name=='addition_growth.py':
            s='from roles_runtime import roles\n'+s
            s=once(s,"        own=frame['own_view']", "        if self.anchor is None:self.strong=roles.strong\n        own=frame['own_view']")
        elif name=='ticket_condition.py':
            s=once(s,"root.name.startswith('target_core_cycle_active_v8_fixed15-core-loop-2026085-')", "root.parent.as_posix()=='C:/BTC5M-worker/.tmp' and root.name.startswith('target_core_cycle_active_v8_fixed15-core-loop-')")
        ast.parse(s);(PACKAGE/name).write_text(s,encoding='utf-8')
    shutil.copy2(ROOT/'tools/btc5m_transfer_roles_v1.py',PACKAGE/'roles_runtime.py')
    for mid in (2026085,*MARKETS):
        if mid==2026085:
            s=read(SOURCE/f'input_{mid}.json.gz')
            public=dict(market={k:s['market'][k] for k in ('market_id','window_start_ms','window_end_ms','quality_status')},
                books=s['books'],public=s['public'],tape=dict(file=f'tapes/{mid}.json.xz',sha256=sha(SOURCE/f'tapes/{mid}.json.xz')),
                actor_input_contract='Current public frame and canonical OWN only; simulator tape is not an actor feature.')
            (PACKAGE/f'inputs/public_{mid}.json.gz').write_bytes(gzip.compress(json.dumps(public,separators=(',',':')).encode(),mtime=0))
        else:shutil.copy2(INPUT/f'public_{mid}.json.gz',PACKAGE/f'inputs/public_{mid}.json.gz')
        shutil.copy2(SOURCE/f'tapes/{mid}.json.xz',PACKAGE/f'inputs/tapes/{mid}.json.xz')
    m=dict(parent);m.update(version=STEM,market=None,oracle_direction=None,maximum_native_jobs=9,
        parent_manifest_sha256=sha(PARENT/'manifest.json'),fixed_qref=result['fixed_train_share_unit'],theta=result['theta'],
        window_source_sha256=old['marketWindowSourceSha256'],remote_inputs={},files={},
        target_path_loaded=False,structural_transfer=True,paired_markets=list(MARKETS))
    m['files']={p.relative_to(PACKAGE).as_posix():sha(p) for p in PACKAGE.rglob('*') if p.is_file()}
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):compile(compiled(PACKAGE,mode),'structural_compiled','exec')
    dump('LOCAL_PREFLIGHT',dict(status='PASS',syntax_modes=3,role_component=binding.self_test(),local_native_jobs=0,
        note='Full mirror decision and parity checks run separately before paired dispatch.'))
    labels=read(R/'BTC5M_CORE_LOOP_TRANSFER_AB_V1_20260913_OFFLINE_LABELS.json')['labels']
    jobs=[dict(job_id=PARITY,market=2026085,arm='PARITY',mode='ORACLE_UP')]
    for mid in MARKETS:
        for arm,mode in [('known','ORACLE_'+labels[str(mid)]['side']),('no_direction','NO_DIRECTION')]:
            jobs.append(dict(job_id=f'fixed15-core-loop-{mid}-transfer-{arm.replace("_","-")}-20260913-v1',market=mid,arm=arm,mode=mode))
    for j in jobs:
        j['argv']=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py','--market-id',str(j['market']),
            '--mode',j['mode'],'--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE']
        j.update(cwd='.',max_threads=4)
    dump('WAVE',dict(jobs=jobs,manifest_sha256=sha(PACKAGE/'manifest.json'),sequential=True))
    print(json.dumps(dict(status='FROZEN',manifest_sha256=sha(PACKAGE/'manifest.json'),files=len(m['files']),jobs=len(jobs))))


if __name__=='__main__':main()
