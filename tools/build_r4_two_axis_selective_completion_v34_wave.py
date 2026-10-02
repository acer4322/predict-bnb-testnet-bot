import json
from pathlib import Path
B=Path('data/research/r4_v0/p0_provenance_v1')
src=json.loads((B/'r4_carrier_pathstate_v32_2_wave.json').read_text(encoding='utf-8'))
jobs=[]
for j in src['jobs']:
    suffix=j['job_id'].split('r4-carrier-v32-2-')[1]
    for kind,mapname in [('cand','r4_two_axis_selective_completion_v34_event_map.json'),('base','r4_two_axis_selective_completion_v34_empty_event_map.json')]:
        jj={k:v for k,v in j.items() if k!='job_id'}
        jj['job_id']=f'r4-v34-{kind}-{suffix}'
        argv=list(jj['argv'])
        argv[1]='.lan_worker_v1\\staging\\run_r4_continuous_exact_first_late_frozen_ids_v25.py'
        ei=argv.index('--event-map')+1
        argv[ei]=f'C:\\BTC5M-worker\\.lan_worker_v1\\staging\\{mapname}'
        jj['argv']=argv
        jobs.append(jj)
plan={'progress_artifact':'data/research/r4_v0/p0_provenance_v1/r4_two_axis_selective_completion_v34_progress.json','jobs':jobs,'postprocess':{'argv':['.venv\\Scripts\\python.exe','tools/aggregate_r4_two_axis_selective_completion_v34.py'],'cwd':'.','timeout_seconds':30,'max_output_bytes':12000,'required_artifact':'data/research/r4_v0/p0_provenance_v1/r4_two_axis_selective_completion_v34_summary.json'}}
(B/'r4_two_axis_selective_completion_v34_wave.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
print(json.dumps({'jobs':len(jobs),'plan':str(B/'r4_two_axis_selective_completion_v34_wave.json')}))
