"""Read only the active task's ETH output/scratch byte counts and worker probe."""
import base64, importlib.util, json, subprocess, sys
from pathlib import Path
sys.dont_write_bytecode = True
P = Path(__file__).resolve().parent
s = importlib.util.spec_from_file_location('d', P.parent/'native_engine_comparison_20261002_v1/lan_actions.py')
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
probe = m.d.cmd_probe('btc5m-worker')
assert probe['hostname'] == 'DESKTOP-JIERAGF'
code = '''import json,pathlib
root=pathlib.Path('C:/BTC5M-worker')
scratch=list((root/'.tmp').glob('target_core_cycle_active_v8_c100_ETHCG1ATV1R2_*'))
r=root/'.lan_worker_v1/results/original-maker-layer-eth861-20261002-v1r2/arms'
finished=[d for d in r.iterdir() if (d/'ORCHESTRATION_AUDIT.json').exists()]
def size(d):return sum(f.stat().st_size for f in d.rglob('*') if f.is_file())
assert all(d.resolve().is_relative_to(root.resolve()) for d in scratch+finished)
sb=sum(size(d) for d in scratch);fb=sum(size(d) for d in finished)
print(json.dumps(dict(finished=len(finished),scratch_dirs=len(scratch),scratch_bytes=sb,
 finished_output_bytes=fb,observed_bytes_per_finished=(sb+fb)/len(finished) if finished else None,
 projected_total_bytes_from_current_mean=(sb+fb)*2583/len(finished) if finished else None,
 note='Descriptive storage projection only; no deletion or experiment change')))
'''
payload = "import base64;exec(base64.b64decode('"+base64.b64encode(code.encode()).decode()+"').decode())"
cp = m.strict('btc5m-worker', subprocess.list2cmdline([m.d.REMOTE_PY, '-c', payload]), timeout=30)
assert cp.returncode == 0, cp.stderr
x = dict(probe=probe, task_owned_sizes=json.loads(cp.stdout.strip()))
(P/'ETH_RESOURCE_BUDGET.json').write_text(json.dumps(x, indent=2))
print(json.dumps(dict(disk_free_gb=probe['disk_free_gb'],memory_free_gb=probe['memory']['free_gb'],
                     cpu_pct=probe['cpu_pct'],task_owned_sizes=x['task_owned_sizes'])))
