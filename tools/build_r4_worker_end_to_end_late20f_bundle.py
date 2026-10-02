from __future__ import annotations
import json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';PRE=P/'r4_management_simulator_end_to_end_trajectory_v1_preregistered.json';OUT=ROOT/'.tmp/r4_worker_end_to_end_late20f_bundle.zip';OUT.parent.mkdir(exist_ok=True)
ids=json.loads(PRE.read_text(encoding='utf-8'))['validationCohort']['marketIds'];files=[PRE,ROOT/'tools/build_r4_management_simulator_end_to_end_late20f_v1.py',ROOT/'tools/test_r4_p0b_pending_submit_reservation_simulator_v1.py',ROOT/'tools/test_r4_rolling_queue_option_lifecycle_shadow_v1.py',ROOT/'tools/test_r4_p0b_provenance_simulator_v1.py']
for mid in ids:files.append(ROOT/f'data/hft_forward_paper_v1/markets/{mid}_r2_hft_closed_loop_v1.json.xz')
with zipfile.ZipFile(OUT,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
 for f in files:
  if f.exists():z.write(f,f.relative_to(ROOT))
print(json.dumps({'bundle':str(OUT.relative_to(ROOT)),'bytes':OUT.stat().st_size,'markets':len(ids),'files':len(files)}))
