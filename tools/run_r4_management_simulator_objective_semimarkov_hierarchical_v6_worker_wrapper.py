from pathlib import Path
import shutil,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
src=ROOT/'.lan_worker_v1/staging/diagnose_r4_management_simulator_objective_semimarkov_hierarchical_v6.py'
dst=ROOT/'tools/diagnose_r4_management_simulator_objective_semimarkov_hierarchical_v6.py'
shutil.copy2(src,dst)
src2=ROOT/'.lan_worker_v1/staging/r4_objv6_dev_rows_compact.json.gz'
dst2=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_objv6_dev_rows_compact.json.gz'
dst2.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src2,dst2)
print('READY')
