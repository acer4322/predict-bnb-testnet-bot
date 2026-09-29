"""Assemble an isolated research package from an exact R87 source pin.

Does not edit R87, AGENTS, CURRENT, strategies, checkpoints, or running services.
"""
from __future__ import annotations
import json
import shutil
import tempfile
from pathlib import Path
from .engine import atomic_json, file_hash, read_json, validate_spec

R87 = 'data/research/btc5m_public_cost_memory_20260921_r87'
R87_MANIFEST_SHA256 = '97f4c9032f32284e990253a2e3738f6590579fe3f84ff6978e05baf5e5cc7f40'
DEFAULT_RUN = 'data/research/btc5m_batch_search_20260921_r88'
LIB = Path(__file__).resolve().parent


def patch_loop(text: str) -> str:
    replacements = [
        ("                w.terminal(key,status[int(o.status)],float(o.qty-o.leaves_qty))",
         "                w.terminal(key,status[int(o.status)],float(o.qty-o.leaves_qty))\n"
         "                policy.observe_terminal(key,status[int(o.status)],float(o.qty-o.leaves_qty),ms)"),
        ("responsibility_observer=policy.last_observer);records.append(row)",
         "responsibility_observer=policy.last_observer,attempt_observer=policy.last_attempt_view);records.append(row)"),
        ("return dict(public_summary=policy.public_cost.snapshot(),",
         "return dict(search_config=policy.search_config,formula_forecasts=policy.formula_forecasts,"
         "attempt_summary=policy.attempt_memory.snapshot(w.observe(),policy.cost_model,policy.entry_seconds),"
         "attempt_terminals=policy.attempt_memory.terminals,public_summary=policy.public_cost.snapshot(),")]
    for old,new in replacements:
        if text.count(old)!=1:
            raise ValueError('R87 integration anchor differs; do not apply a fuzzy patch')
        text=text.replace(old,new)
    compile(text,'run_loop.py','exec')
    return text


def verify_package(directory: Path) -> dict:
    directory=directory.resolve()
    manifest=read_json(directory/'MANIFEST.json')
    if manifest.get('version') != 'R88_BATCH_SEARCH_V1':
        raise ValueError('This is not the declared batch-search package')
    for name, expected in manifest['files'].items():
        path=(directory/name).resolve()
        if not path.is_relative_to(directory) or file_hash(path)!=expected:
            raise ValueError('Frozen package mismatch: '+name)
    return manifest


def prepare(repo: Path, run_dir=DEFAULT_RUN, spec_path=None) -> Path:
    repo=repo.resolve();source=(repo/R87).resolve();dest=(repo/run_dir).resolve()
    if not source.is_relative_to(repo) or not dest.is_relative_to(repo/'data/research'):
        raise ValueError('Source/destination outside research scope')
    if dest==source:
        raise ValueError('Cannot overwrite R87')
    if (dest/'MANIFEST.json').exists():
        verify_package(dest)
        if spec_path is not None and read_json(spec_path)!=read_json(dest/'SEARCH_SPEC.json'):
            raise ValueError('Different spec requires a new run directory')
        return dest
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError('Nonempty unfrozen directory; inspect it rather than overwrite')
    if file_hash(source/'MANIFEST.json')!=R87_MANIFEST_SHA256:
        raise ValueError('R87 manifest differs from the reviewed source')
    parent=read_json(source/'MANIFEST.json')
    for name,sha in parent['files'].items():
        path=(source/name).resolve()
        if not path.is_relative_to(source) or not path.is_file() or file_hash(path)!=sha:
            raise ValueError('Frozen R87 source changed: '+name)
    spec=read_json(spec_path or LIB/'default_spec.json');validate_spec(spec)
    expected_objectives=['mean_pending_conservative_floor','mean_paired_surplus','mean_best_branch']
    if spec['objectives']!=expected_objectives:
        raise ValueError('This adapter supports only its declared three objectives')
    scope=spec['scope']
    if scope.get('capital_cap') is not None or scope.get('live_authority') or scope.get('neural_updates')!=0 or scope.get('market')!='1977248':
        raise ValueError('Research scope changed; requires a separately reviewed adapter')
    if set(spec['space'])!={'history','curve','failure_weight','age_weight'}:
        raise ValueError('Formula parameter schema differs')
    if not set(spec['space']['history']['values']).issubset({'attempts','age','attempts_age'}):
        raise ValueError('Unsupported history family')
    if not set(spec['space']['curve']['values']).issubset({'linear','sqrt','saturating'}):
        raise ValueError('Unsupported formula family')
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix=dest.name+'_assembly_',dir=dest.parent))
    skip={'prepare.py','dispatch.py','worker.py','freeze.py','micro_screen.py'}
    copies={}
    try:
        for name,sha in parent['files'].items():
            path=Path(name)
            keep=(path.suffix=='.py' and path.name not in skip) or name in (
                'PUBLIC_1977248.json.gz','EXECUTION_1977248.json.gz','SOURCE_PINS.json','LEARNING_PARENT_PINS.json') or name.startswith('traces/CELL_RECENT_')
            if not keep:continue
            if (source/name).stat().st_size>50*1024*1024:
                raise ValueError('Unexpected large inherited artifact')
            newname={'controller.py':'controller_r87.py','checks.py':'checks_r87.py'}.get(name,name)
            target=temp/newname;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/name,target)
            copies[newname]=dict(source=R87+'/'+name,sha256=sha)
        loop=(temp/'run_loop.py').read_text(encoding='utf-8')
        (temp/'run_loop.py').write_text(patch_loop(loop),encoding='utf-8',newline='\n')
        for old,new in [('engine.py','engine.py'),('attempt_memory.py','attempt_memory.py'),
                        ('controller_overlay.py','controller.py'),('worker_template.py','worker.py'),
                        ('component_checks.py','batch_component_checks.py')]:
            shutil.copyfile(LIB/old,temp/new)
        atomic_json(temp/'SEARCH_SPEC.json',spec)
        atomic_json(temp/'PARENT_PINS.json',dict(parent_manifest_sha256=R87_MANIFEST_SHA256,
            parent_files=copies,patched_loop_sha256=file_hash(temp/'run_loop.py')))
        (temp/'PROTOCOL.md').write_text(PROTOCOL,encoding='utf-8')
        for path in temp.rglob('*.py'):
            compile(path.read_text(encoding='utf-8'),str(path),'exec')
        files={str(p.relative_to(temp)).replace('\\','/'):file_hash(p) for p in temp.rglob('*') if p.is_file()}
        atomic_json(temp/'MANIFEST.json',dict(version='R88_BATCH_SEARCH_V1',files=files,
            source_r87_manifest=R87_MANIFEST_SHA256,capital_cap=None,max_threads=4,
            numerical_threads=1,nn_training_updates=0,live_changes=0,declared_trial_budget=spec['trials']))
        verify_package(temp)
        if dest.exists():dest.rmdir()  # Only the empty pre-existing directory is permitted.
        temp.replace(dest)
        return dest
    except Exception:
        # Preserve the partial assembly for diagnosis. Never delete user files.
        raise


PROTOCOL='''# R88 batch search — preregistered research-only package

Source: exact reviewed R87 manifest; R87 and R65/R84 weights/Adam unchanged.
Distinct experiment: native receipt-conditioned failed ACTIVE repair attempts
and true fill-lot age, used as soft marginal cost on new PASSIVE exposure.
R59's canonical terminal-zero semantics are reused; its failed hard retry mask
is explicitly excluded. R60/R65 caution about teacher/retention failures applies.
This is neither a fresh NN nor a claim that the existing NN learned new inputs.

A 12-candidate initial batch uses early/250/0 and late/750/500 execution cases.
Each candidate receives a fresh full native trajectory, not substituted historic
fills. First run must reproduce one R85 observer-only control in 10 field groups.
All 12 are full-market single-market diagnostics, not 12 independent markets.

Search: seeded random global exploration plus Pareto-neighbor mutation after
9 completed candidates; 35% global exploration retained. No arbitrary eval of
formula strings. Whitelisted history/curve families and bounded numeric weights.
Three maximized objectives: mean pending-conservative floor, paired surplus,
and best observed branch. Both outcome branches and real cost are always saved.
No actual winner/Target signal is supplied to the policy or optimizer objectives.

Select up to 3 nondominated candidates by objective extremes. Freeze shortlist
before evaluating the remaining 10 scenarios. These are known/consumed execution
sensitivity scenarios, NOT fresh holdout/OOS. Keep every deterioration and every
censored/unresolved case. The no-history ablation is the exactly reproduced R85
reference. Formula coefficient neighborhoods are not declared robust from a
single sampled optimum; broader parameter-neighbor validation remains a next gate.

Continuation gates inherit R87: all endpoints owner-closed; preserve the three
original closed nonnegative paths; positive mean paired surplus; improved mean
floor; more common-closed improvements than deteriorations; post90 cycles in at
least6 scenarios. Post90 is an evaluation slice, NOT a runtime ban or delay.
Passing still does not promote a policy, train a NN, or authorize live capital.

One heavy job, max4 threads, numerical libraries1 thread; second PC only.
capital_cap=null. Venue legality, partial fills, costs, pending/UNKNOWN ownership,
cancel acknowledgement, no-self-cross and source/receive clocks remain inherited.
The data retain conditional200bps and unknown source-clock/venue-fee certification.

Every proposal, completed scenario and batch is atomically saved. Cached results
require package/config/scenario and trace hashes. Incomplete scenarios stop, not
silently retry. An exact known job must be reconciled before resubmitting anything.
Completed batch continuation uses a NEW job and a copied verified study/cache;
the prior job/results remain immutable. No automatic multi-market expansion.
'''
