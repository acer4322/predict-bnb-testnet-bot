from __future__ import annotations
import hashlib,importlib.util,json,sys
from pathlib import Path
ROOT=Path.cwd().resolve()
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
fr=json.loads(Path('B3_HANDOFF_NATIVE_BUNDLE_ONESHOT_SMOKE4_V1_EXECUTION_FREEZE_20260908.json').read_text(encoding='utf-8'))
exp=fr['sha256']
paths={
'runner':'run_b3_handoff_native_bundle_oneshot_smoke4_v1.py',
'treatmentPreflightRunner':'tools/run_b3_handoff_native_bundle_treatment_preflight_v1.py',
'manifestBuilder':'tools/build_b3_handoff_native_bundle_oneshot_manifest_v1.py',
'manifest':'B3_HANDOFF_NATIVE_BUNDLE_ONESHOT_SMOKE4_V1_PREREG_20260908.json',
'treatmentPreflight':'B3_HANDOFF_NATIVE_BUNDLE_ONESHOT_SMOKE4_V1_TREATMENT_PREFLIGHT_20260908.json',
'cohort':'NATIVE_SELECTION_MARGIN_PREVALENCE_STAGEA16_V1_COHORT_20260908.json',
'bundle':'v16_consumed_holdout100_bundle.zip',
'stageA16Runner':'tools/run_native_selection_margin_prevalence_stagea16_v1.py',
'b2Runner':'tools/run_b2_matched_state_topology_contract_smoke4_v1.py',
'v3bRuntime':'tools/run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate.py'}
got={k:(sha(v) if Path(v).exists() else None) for k,v in paths.items()};checks={k:got[k]==exp[k] for k in paths}
# Import the actual runner and capture the modules it resolves through the staging tools package.
spec=importlib.util.spec_from_file_location('b3run',Path(paths['runner']).resolve());m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
imports={'runner':str(Path(m.__file__).resolve()),'manifestBuilder':str(Path(m.mf.__file__).resolve()),'treatmentPreflightRunner':str(Path(m.tp.__file__).resolve()),'stageA16Runner':str(Path(m.margin.__file__).resolve()),'b2Runner':str(Path(m.b2.__file__).resolve()),'v3bRuntime':str(Path(m.b2.v3b.__file__).resolve())}
for k in ('manifestBuilder','treatmentPreflightRunner','stageA16Runner','b2Runner','v3bRuntime'):
    checks[k+'ImportPath']=Path(imports[k]).resolve()==Path(paths[k]).resolve()
out={'checks':checks,'allMatch':all(checks.values()),'got':got,'expected':{k:exp[k] for k in paths},'importPaths':imports}
Path(__import__('os').environ['BTC5M_LAN_RESULT_DIR'],'result.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps({'allMatch':out['allMatch'],'checks':checks}))
