from pathlib import Path
import json
root=Path('.')
src=json.loads((root/'data/research/r4_v0/p0_provenance_v1/r4_exact_first_late_v25_wave.json').read_text(encoding='utf-8'))
out={'progress_artifact':'data/research/r4_v0/p0_provenance_v1/r4_runtime_two_axis_v37_progress.json','jobs':[]}
for i,j in enumerate(src['jobs']):
 jj=dict(j);jj['job_id']=f'r4-runtime2axis-v37-{i:02d}'
 a=list(jj['argv']);a[1]='.lan_worker_v1\\staging\\run_r4_runtime_two_axis_frozen_ids_v37.py'
 a=[('--seam-map' if x=='--event-map' else x) for x in a]
 jj['argv']=a
 out['jobs'].append(jj)
out['postprocess']={'argv':['python','tools/aggregate_r4_runtime_two_axis_v37.py'],'cwd':'.','timeout_seconds':30,'max_output_bytes':16000,'required_artifact':'data/research/r4_v0/p0_provenance_v1/r4_runtime_two_axis_v37_equivalence.json'}
p=root/'data/research/r4_v0/p0_provenance_v1/r4_runtime_two_axis_v37_wave.json';p.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(p)
