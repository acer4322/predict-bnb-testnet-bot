from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[1]
for rel in ['tools/diagnose_r4_management_simulator_objective_semimarkov_hierarchical_v6.py','data/research/r4_v0/p0_provenance_v1/r4_objv6_dev_rows_compact.json.gz']:
 src=ROOT/'.lan_worker_v1/staging'/Path(rel).name
 dst=ROOT/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
print('READY')
