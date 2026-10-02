"""Build immutable oracle diagnostic from the verified intent wrapper; no fitting."""
import ast
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT/'.lan_worker_v1/exposure_intent_2026085_20260913_v1'
PACKAGE = ROOT/'.lan_worker_v1/oracle_repair_2026085_20260913_v1'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def once(source, old, new):
    assert source.count(old) == 1, (old, source.count(old))
    return source.replace(old, new, 1)


def main():
    previous = json.loads((OLD/'manifest.json').read_text())
    for name, digest in previous['files'].items(): assert sha(OLD/name) == digest
    source = (OLD/'run_btc5m_exposure_intent_ablation_v1.py').read_text()
    source = once(source, "MODES = ('CONTROL', 'FOLLOW_CURRENT', 'LATCH_FIRST_CONFIRMED')", "MODES = ('ORACLE_UP', 'ORACLE_DOWN')")
    start = source.index('    def apply(self, legacy, own_net, frame):')
    end = source.index('    def capture(', start)
    source = source[:start] + '''    def apply(self, legacy, own_net, frame):
        self.sign = 1 if self.mode == 'ORACLE_UP' else -1
        if self.birth is None:
            self.birth = dict(t=int(frame['t']), index=int(frame['index']), sign=self.sign,
                provenance='OFFLINE_FINAL_NET_DIRECTION_CONDITION', inv=dict(frame['own_view']['inv']))
        return self.sign * abs(legacy)

''' + source[end:]
    start = source.index('def self_test():'); end = source.index('\n\ndef main():', start)
    source = source[:start] + '''def self_test():
    f = dict(t=1, index=1, own_view=dict(inv=dict(UP=0., DOWN=0.)))
    for mode in MODES:
        c = ExposureIntent(mode); sign = 1 if mode == 'ORACLE_UP' else -1
        for legacy, net in [(0., 0.), (-.2, .3), (.4, -.5), (0., 0.)]:
            assert c.apply(legacy, net, f) == sign * abs(legacy)
            assert c.sign == sign
        assert c.birth['provenance'] == 'OFFLINE_FINAL_NET_DIRECTION_CONDITION'
''' + source[end:]
    source = once(source, "ap.add_argument('--check-only', action='store_true'); args = ap.parse_args(); self_test()", """ap.add_argument('--repair-route', choices=('NONE','PASSIVE','ACTIVE'), default='NONE')
    ap.add_argument('--repair-time', type=int, default=-1)
    ap.add_argument('--repair-side', choices=('UP','DOWN'), default='DOWN')
    ap.add_argument('--check-only', action='store_true'); args = ap.parse_args(); self_test()
    assert (args.repair_route == 'NONE') == (args.repair_time == -1)""")
    source = once(source, "    compile(source, str(package/'frozen_runner.py'), 'exec')", '''    source = replace(source, "target_runtime_access=False,target_scoring_only=True", "target_runtime_access=True,target_scoring_only=False")
    source = replace(source, "dream_fill=False,funding_mode=", "oracle=True,target_direction_input=True,runtime_eligible=False,lookahead_condition='TARGET_FINAL_OBSERVED_NET_SIDE',oracle_direction=_MODE,repair_route=_REPAIR_ROUTE,dream_fill=False,funding_mode=")
    source = replace(source, "purpose='EXACT_SAME_FRONTIER_PASSIVE_VS_ACTIVE_ROUTE_VALUE_CONSUMED8_LINEAGE_STALL_TEST'", "purpose='ORACLE_FIXED_DIRECTION_REPAIR_DIAGNOSTIC'")
    compile(source, str(package/'frozen_runner.py'), 'exec')''')
    source = once(source, "_MODE=args.mode, _ExposureIntent=ExposureIntent)", "_MODE=args.mode, _REPAIR_ROUTE=args.repair_route, _ExposureIntent=ExposureIntent)")
    source = once(source, "target_direction_input=False, oracle=False, numeric_parameters_frozen=True,", "target_direction_input=True, oracle=True, runtime_eligible=False, numeric_parameters_frozen=True, repair_route=args.repair_route, repair_time=args.repair_time,")
    source = once(source, "limitation='First own inventory side is an experimental seed, not identified Target intent.'", "limitation='Future-derived direction bit only. Not runtime eligible. Passive/Active branch uses own prefix checkpoint, not Target timing.'")
    source = once(source, "    namespace['main']()", """    if args.repair_route != 'NONE':
        sys.argv += ['--exact-frontier-time', str(args.repair_time), '--exact-frontier-side', args.repair_side,
                     '--exact-frontier-kind', 'REPAIR', '--exact-frontier-route', args.repair_route]
    namespace['main']()""")
    first_end = source.index('"""', 3) + 3
    source = '"""Offline oracle-direction diagnostic with a same-prefix repair-route fork. Not deployable."""' + source[first_end:]
    ast.parse(source)
    assert not PACKAGE.exists(), 'immutable package already exists'
    PACKAGE.mkdir()
    for name in ('frozen_runner.py','helper_rearm.py','adapter.py','clock_wrapper.py'): shutil.copy2(OLD/name, PACKAGE/name)
    (PACKAGE/'oracle_runner.py').write_text(source, encoding='utf-8')
    # Execute only the controller class/test AST; no wrapper main or model/native imports.
    tree = ast.parse(source); nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in ('ExposureIntent','self_test')]
    env = {'MODES':('ORACLE_UP','ORACLE_DOWN')}; exec(compile(ast.Module(body=nodes,type_ignores=[]), '<controller-test>', 'exec'), env); env['self_test']()
    manifest = dict(version='BTC5M_ORACLE_REPAIR_PANEL_PREREG_V1', market=2026085,
        oracle=True, runtime_eligible=False, target_direction_input=True, oracle_side='UP',
        oracle_definition='Final observed Target BID-acquisition net side, not settlement winner or private initial intent.',
        hypotheses=['Oracle DOWN must reproduce existing LATCH_FIRST economic/trace baseline.',
            'Fix oracle UP, then first own aligned DOWN REPAIR frontier with qty <= current net is the predeclared route fork.',
            'Only selected repair route changes. Numeric theta, direction, public tape, pending prefix and Fresh rules remain frozen. Realized continuation is endogenous.'],
        selection_rule='Earliest repair_capacity_frontier_rows DOWN with qty>0 and qty<=atomic_repair_need; own UP surplus at same frame. No future fill, PnL or Target action selection.',
        primary='Old responsibility first/full payment and debt-time area; selected carrier fill is a separate metric.',
        secondary='Completion before sign reversal, repair/expand continuation, terminal residual, floor/best and Target topology.',
        no_parameter_search=True, fixed_train_total_frames=1487.0, max_threads=4,
        backend=previous['backend'], native_sha256=previous['native_sha256'], remote_inputs=previous['remote_inputs'],
        files={p.name:sha(p) for p in PACKAGE.iterdir()}, parent_manifest_sha256=sha(OLD/'manifest.json'))
    (PACKAGE/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (ROOT/'data/research/BTC5M_ORACLE_REPAIR_PANEL_PREREGISTERED_20260913.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for tag, mode in [('down-control','ORACLE_DOWN'),('up-baseline','ORACLE_UP')]:
        wave = dict(progress_artifact='data/research/ORACLE_REPAIR_'+tag.upper().replace('-','_')+'_PROGRESS_20260913.json',
            jobs=[dict(job_id='oracle-repair-2026085-'+tag+'-20260913-v1',
                argv=['.venv/Scripts/python.exe','.lan_worker_v1/staging/'+PACKAGE.name+'/oracle_runner.py','--mode',mode],
                cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
        (ROOT/('data/research/oracle_repair_'+tag+'_wave_20260913.json')).write_text(json.dumps(wave,indent=2)+'\n')
    print(json.dumps(dict(package=str(PACKAGE),controller_test='PASS',files=manifest['files'])))


if __name__=='__main__': main()
