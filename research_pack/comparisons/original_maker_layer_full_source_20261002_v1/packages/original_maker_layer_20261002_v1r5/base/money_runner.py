"""Offline oracle-direction diagnostic with a same-prefix repair-route fork. Not deployable."""
from __future__ import annotations
import argparse
import collections
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import sys

from roles_runtime import roles, transform_policy
MODES = ('ORACLE_UP', 'ORACLE_DOWN', 'NO_DIRECTION')


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


class ExposureIntent:
    def __init__(self, mode):
        assert mode in MODES
        self.mode = mode; self.sign = 0; self.birth = None; self.rows = []
        self.held_amplitude = None; self.amplitude_birth = None; self.amplitude_rows = []

    def apply(self, legacy, own_net, frame):
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

    def capture(self, frame, producer, ledger, legacy, applied, desired):
        totals = producer.atomic.totals()
        accounts = {s: ledger.account(pid) for pid, s in ((1, 'UP'), (2, 'DOWN'))}
        carriers = ledger.carriers
        self.rows.append(dict(t=int(frame['t']), index=int(frame['index']), gateway_state_id=frame['gateway_state_id'],
            inv=dict(frame['own_view']['inv']), cost=float(frame['own_view']['cost']),
            legacy_exposure=legacy, applied_exposure=applied, retained_direction_sign=self.sign,
            desired=dict(desired), atomic_outstanding=totals, atomic_completed=producer.atomic.completed,
            reserved_qty={s: a['reserved_qty'] for s, a in accounts.items()},
            reserved_cash={s: a['reserved_cash'] for s, a in accounts.items()},
            carrier_states=dict(collections.Counter(c.state for c in carriers.values())),
            terminal_total=sum(c.state == 'TERMINAL' for c in carriers.values()),
            first_direction_birth=self.birth))


def self_test():
    from roles_runtime import self_test as role_test
    role_test()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--market-id', type=int, default=2026085); ap.add_argument('--mode', choices=MODES, required=True)
    ap.add_argument('--money-mode', choices=('INERT', 'PARALLEL_QUANTITY', 'PARALLEL_PAYOFF_ZERO'), required=True)
    ap.add_argument('--demand-mode', choices=('LEGACY_PERSIST', 'AUTO_CAP_ONLY', 'AUTO_REPAIR'), required=True)
    ap.add_argument('--retention',type=float,choices=(0.,),required=True)
    ap.add_argument('--opportunity-mode',choices=('CONTROL','ONE_ACTIVE'),required=True)
    ap.add_argument('--repair-route', choices=('NONE','PASSIVE','ACTIVE'), default='NONE')
    ap.add_argument('--repair-time', type=int, default=-1)
    ap.add_argument('--repair-side', choices=('UP','DOWN'), default='DOWN')
    ap.add_argument('--direction-rule', choices=('LEGACY','INVENTORY','REPAIR_GRACE'), default='LEGACY')
    ap.add_argument('--check-only', action='store_true'); args = ap.parse_args(); self_test()
    assert (args.repair_route == 'NONE') == (args.repair_time == -1)
    package = Path(__file__).resolve().parent; manifest = json.loads((package/'manifest.json').read_text())
    for name, digest in manifest['files'].items(): assert sha(package/name) == digest, name
    assert args.mode in MODES and args.repair_route=='NONE' and args.retention==0. and args.demand_mode=='AUTO_REPAIR'
    gate=load('payoff_repair_gate',package/'money_gate.py');gate.self_test()
    wrapper = load('intent_clock_wrapper', package/'clock_wrapper.py')
    source = wrapper.transformed(package, manifest, 'FIXED_TRAIN_EVENTS'); replace = wrapper.replace_once
    source = replace(source, 'self.total_frames=1;self.clock_observations=[]',
        'self.total_frames=1;self.clock_observations=[];self.intent=_ExposureIntent(_MODE)')
    original = "exposure=math.tanh(w[3]*x['own_net']);up=(1.+exposure)/2."
    source = replace(source, original,
        "legacy_exposure=math.tanh(w[3]*x['own_net']);exposure=self.intent.apply(legacy_exposure,x['own_net'],f);up=(1.+exposure)/2.")
    marker = "    for s in pid:\n"
    source = replace(source, marker,
        "    self.intent.capture(f,self,ledger,legacy_exposure,exposure,desired)\n" + marker)
    source = replace(source, " except Exception as ex:\n  result['status']='ERROR'",
        " except Exception as ex:\n  result['failure_capture']=_save_failure(out,locals())\n  result['status']='ERROR'")
    source = replace(source, "target_runtime_access=False,target_scoring_only=True", "target_runtime_access=True,target_scoring_only=False")
    source = replace(source, "dream_fill=False,funding_mode=", "cash_budget_enabled=False,oracle=True,target_direction_input=True,runtime_eligible=False,lookahead_condition='TARGET_FINAL_OBSERVED_NET_SIDE',oracle_direction=_MODE,repair_route=_REPAIR_ROUTE,dream_fill=False,funding_mode=")
    source = replace(source, "purpose='EXACT_SAME_FRONTIER_PASSIVE_VS_ACTIVE_ROUTE_VALUE_CONSUMED8_LINEAGE_STALL_TEST'", "purpose='ORACLE_FIXED_DIRECTION_REPAIR_DIAGNOSTIC'")
    source=gate.instrument(source,replace)
    demand=load('single_repair_demand',package/'demand_gate.py');demand.self_test()
    budget=load('profit_budget',package/'cash_budget.py');budget.self_test();budget_class=budget.make_capacity(demand.WholeCapacity)
    source=demand.instrument(source,replace)
    ticket=load('fixed15_condition',package/'ticket_condition.py')
    source=ticket.instrument(source,replace)
    opportunity=load('active_opportunity',package/'active_opportunity.py')
    source=opportunity.instrument(source,replace)
    commitment_repair=load('commitment_repair_probe',package/'commitment_repair.py')
    source=commitment_repair.instrument(source,replace)
    coordination=load('reexposure_coordination',package/'coordination.py')
    source=coordination.instrument(source,replace)
    general_finite_active=load('general_finite_active',package/'general_finite_active.py')
    source=general_finite_active.instrument(source,replace)
    maintenance_scope=load('maintenance_scope',package/'maintenance_scope.py')
    source=maintenance_scope.instrument(source,replace)
    addition_growth=load('addition_growth',package/'addition_growth.py');addition_growth.self_test()
    source=addition_growth.instrument(source,replace)
    growth_hold=load('growth_hold',package/'growth_hold.py')
    source=growth_hold.instrument(source,replace)
    failure_add=load('failure_add_admission',package/'failure_add_admission.py');failure_add.self_test()
    source=failure_add.instrument(source,replace)
    # Pin existing numeric training reference; no test Target source is loaded in this process.
    begin=source.index("  old=json.loads((Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_training_rules_v2_clockfix_20260910')")
    end=source.index("  sys.path.insert(0,str(root));",begin)
    source=source[:begin]+"  public_path=Path(__file__).parent/'inputs'/f'public_{a.market}.json.gz'\n  source=json.loads(gzip.decompress(public_path.read_bytes()))\n  assert set(source)=={'market','books','public','tape','actor_input_contract'}\n  run_window=[source['market']['window_start_ms'],source['market']['window_end_ms']]\n  run_window_sha=_WINDOW_SOURCE_SHA\n  tpfile=Path(__file__).parent/'inputs'/'tapes'/f'{a.market}.json.xz'\n  (root/'tapes').mkdir(parents=True,exist_ok=True)\n  shutil.copy2(tpfile,root/'tapes'/f'{a.market}.json.xz')\n"+source[end:]
    begin=source.index('  train_sources=');end=source.index('  class Policy:',begin)
    source=source[:begin]+"  qref=_FIXED_QREF;theta=list(_FIXED_THETA);start,end=run_window;tp=None\n"+source[end:]
    source=replace(source,'    self.calls+=1;ops=[];', '    roles.observe(f)\n    self.calls+=1;ops=[];')
    source=replace(source,'    x=stable_features(f)','    x=roles.features(stable_features(f))')
    source=replace(source,"pl=path_loss(tr.states,source,terminal,qref);op=helper.own_profile(tr.states,start,end);score=helper.structural_score(pl['path_mse'],tp,op)",
        "pl=dict(path_mse=None,coordinate_mse=None);op=helper.own_profile(tr.states,start,end);score={}")
    source=replace(source,'target_runtime_access=True,target_scoring_only=False',
        "target_runtime_access=False,target_scoring_only=False")
    source=replace(source,"oracle=True,target_direction_input=True", "oracle=(_MODE!='NO_DIRECTION'),target_direction_input=(_MODE!='NO_DIRECTION')")
    source=replace(source,"lookahead_condition='TARGET_FINAL_OBSERVED_NET_SIDE'", "lookahead_condition=('TARGET_FINAL_OBSERVED_NET_SIDE' if _MODE!='NO_DIRECTION' else None)")
    source=transform_policy(source)
    tail=load('tail_new_stop',package/'tail_new.py');tail.self_test()
    source=tail.instrument(source,replace)
    bridge=load('dynamic_direction_bridge',package/'direction_bridge.py')
    assert args.direction_rule=='LEGACY' or args.mode=='NO_DIRECTION'
    source=bridge.instrument(source,replace)
    roles.configure('NO_DIRECTION' if args.mode=='NO_DIRECTION' else 'KNOWN_FINAL_DIRECTION',
                    None if args.mode=='NO_DIRECTION' else ('UP' if args.mode=='ORACLE_UP' else 'DOWN'))
    compile(source, str(package/'frozen_runner.py'), 'exec')
    assert socket.gethostname().upper() == 'DESKTOP-JIERAGF', 'worker only'
    for path, digest in manifest['remote_inputs'].items(): assert sha(Path(path)) == digest, path
    backend = Path(manifest['backend']); binary = backend/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    assert sha(binary) == manifest['native_sha256']
    if args.check_only:
        sys.path.insert(0, str(backend)); import hftbacktest
        assert Path(hftbacktest.__file__).resolve().parent == (backend/'hftbacktest').resolve()
        print(json.dumps(dict(status='PASS', worker=socket.gethostname(), mode=args.mode,
            checks='frozen hashes, native import, syntax, flat retention, own reversal, side symmetry; no replay'))); return
    assert int(os.environ.get('OMP_NUM_THREADS', '999')) <= 4
    namespace = dict(_FailureAddAdmission=failure_add.FailureAddAdmission, _DirectionBridge=bridge.DirectionBridge, _DIRECTION_RULE=args.direction_rule, _TailNewStop=tail.TailNewStop, roles=roles, _FIXED_QREF=manifest['fixed_qref'], _FIXED_THETA=manifest['theta'], _WINDOW_SOURCE_SHA=manifest['window_source_sha256'], __name__='intent_frozen', __file__=str(package/'frozen_runner.py'), _MODE=args.mode, _OPPORTUNITY_MODE=args.opportunity_mode, _ActiveOpportunity=opportunity.ActiveOpportunity, _CommitmentRepairProbe=maintenance_scope.make_probe(commitment_repair), _CoordinationProbe=coordination.CoordinationProbe, _GeneralFiniteActive=general_finite_active.GeneralFiniteActive, _install_ticket_condition=ticket.install, _REPAIR_ROUTE=args.repair_route, _ExposureIntent=ExposureIntent, _AdditionGrowth=addition_growth.AdditionGrowth, _GrowthHold=growth_hold.GrowthHold, _MoneyGate=lambda selection,mode:budget_class(selection,mode,args.demand_mode,args.retention), _MONEY_MODE=args.money_mode, _MONEY_SELECTION=manifest['selection'], _DemandGate=demand.SingleRepairDemand, _DEMAND_MODE=args.demand_mode, _DEMAND_SELECTION=manifest['demand_selection'], _OWN_SNAPSHOT=gate.snapshot)

    def write_trace(out, trace, producer, name):
        payload = dict(failure_add_admission_rows=producer.failure_add_admission.rows,failure_add_admission_admissions=producer.failure_add_admission.admission_rows,tail_new_rows=producer.tail_new.rows,growth_hold_rows=producer.growth_hold.rows,growth_hold_arm=producer.growth_hold.armed,growth_hold_events=producer.growth_hold.events,direction_rows=roles.rows, addition_growth_rows=producer.addition_growth.rows, states=trace.states, plans=trace.clock_plans, native_actions=trace.actions,
            observations=producer.clock_observations, intent=producer.intent.rows, exposure_amplitude_rows=producer.intent.amplitude_rows, money_events=producer.money_gate.events, money_rows=producer.money_gate.rows, demand_events=producer.demand.events, demand_rows=producer.demand.rows, demand_owner_rows=producer.demand.owner_rows, demand_plan_rows=producer.demand.plan_rows, demand_maintenance_rows=producer.demand.maintenance_rows, demand_final=producer.demand.final,profit_budget_rows=producer.money_gate.budget_rows,renewal_rows=producer.coordination.renewal.rows,renewal_events=producer.coordination.renewal.memory.events,renewal_work=producer.coordination.renewal.memory.work,renewal_submissions=producer.coordination.renewal.submissions,coordination_rows=producer.coordination.rows,coordination_episode=producer.coordination.episode,coordination_submissions=producer.coordination.submissions,maintenance_scope_rows=producer.commitment_repair.scope_rows,commitment_maintenance_rows=producer.commitment_repair.maintenance_rows,commitment_repair_rows=producer.commitment_repair.rows,commitment_repair_first=producer.commitment_repair.first,commitment_repair_submissions=producer.commitment_repair.submissions,opportunity_rows=producer.opportunity.rows,opportunity_first=producer.opportunity.first,opportunity_submissions=producer.opportunity.submissions,general_finite_active_rows=producer.general_finite_active.rows,general_finite_active_submissions=producer.general_finite_active.submissions)
        payload.update(producer.bridge.trace_payload())
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        with gzip.GzipFile(filename=str(out/name), mode='wb', mtime=0) as stream: stream.write(raw)
        return hashlib.sha256(raw).hexdigest()

    def save_trace(out, trace, producer, actual_frames):
        digest = write_trace(out, trace, producer, 'clock_trace.json.gz')
        return dict(direction_rule=args.direction_rule, direction_switches=sum(r.get('previous') is not None and r['side']!=r.get('previous') for r in producer.bridge.decisions), repair_grace_frames=sum(r['guard'] is not None for r in producer.bridge.decisions), mode='FIXED_TRAIN_EVENTS', intent_mode=args.mode, money_mode=args.money_mode, demand_mode=args.demand_mode, profit_retention=args.retention, cash_budget_enabled=False, opportunity_mode=args.opportunity_mode, demand_visited=producer.demand.visited, actual_replay_frames=actual_frames,
            runtime_clock_total_frames=producer.total_frames, trace_file='clock_trace.json.gz', trace_payload_sha256=digest,
            manifest_sha256=sha(package/'manifest.json'), transformed_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            final_pending_cash_direct={s:a['reserved_cash'] for s,a in producer.demand.final['final_accounts'].items()}, direction_mode=roles.mode, selected_direction=roles.side, role_semantics='Historical UP-named scalar metrics mean strong; side dictionaries and receipts remain physical', first_direction_birth=producer.intent.birth, amplitude_mode='FIRST_CONFIRMED_OWN_HELD', amplitude_birth=producer.intent.amplitude_birth, held_amplitude=producer.intent.held_amplitude, controller_rows=len(producer.intent.rows), general_finite_active_count=len(producer.general_finite_active.submissions),
            target_direction_input=(args.mode!='NO_DIRECTION'), oracle=(args.mode!='NO_DIRECTION'), runtime_eligible=False, numeric_parameters_frozen=True, passive_ticket=15., sizing_condition=ticket.CONTRACT, repair_route=args.repair_route, repair_time=args.repair_time,
            limitation='Structural consumed-market transfer. Known arm has final Target direction only; no-direction uses first confirmed OWN net. Dynamic arms use dual neutral opening and current confirmed OWN inventory; physical repair memory. No test Target path in actor process.')

    def save_failure(out, context):
        capture = dict(status='PARTIAL', fields_missing='UNKNOWN', intent_mode=args.mode)
        try:
            if context.get('tr') is not None and context.get('producer') is not None:
                capture['trace_payload_sha256'] = write_trace(out, context['tr'], context['producer'], 'failure_trace.json.gz')
            sim = context.get('sim')
            if sim is not None:
                (out/'failure_receipts.json').write_text(json.dumps(getattr(sim, '_receipt_delta_rows', []), indent=2)+'\n')
        except Exception as error: capture['capture_error'] = repr(error)
        return capture

    namespace.update(_save_clock_trace=save_trace, _save_failure=save_failure)
    exec(source, namespace); original_loader = namespace['load_module']

    def loader(name, path):
        module = original_loader(name, path)
        if path == namespace['HELPER_PATH']:
            class Trace(module.TraceLite):
                def __init__(self): super().__init__(); self.clock_plans = []
                def plan(self, event):
                    super().plan(event); self.clock_plans.append(dict(t=self.now, operations=event['operations']))
            module.TraceLite = Trace
        return module

    namespace['load_module'] = loader
    sys.argv = [str(package/'frozen_runner.py'), '--market', str(args.market_id), '--route-mode', 'PASSIVE_ONLY',
        '--rearm-mode', 'RESPONSIBILITY_DUAL_CAPACITY']
    if args.repair_route != 'NONE':
        sys.argv += ['--exact-frontier-time', str(args.repair_time), '--exact-frontier-side', args.repair_side,
                     '--exact-frontier-kind', 'REPAIR', '--exact-frontier-route', args.repair_route]
    namespace['main']()


if __name__ == '__main__': main()
