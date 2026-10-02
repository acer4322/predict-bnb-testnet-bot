from pathlib import Path
import sys, json
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
OUT=ROOT/'data/research/r3_v0/r3_hft_ab_market1636523_base_trace_v0.json'
r=run_smoke(1636523,passive_mode='offset0',own_state_poll_ms=250,trace_execution_states=True,strict_past_trace_gate=lambda: True)
OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
print(json.dumps({'ok':True,'report':str(OUT),'decision':r['decision'],'steps':r['controller']['steps'],'makerFills':r['r2ObjectiveExecution']['makerFills'],'takerFills':r['r2ObjectiveExecution']['takerFills'],'runtimeSeconds':r['runtimeSeconds']},ensure_ascii=False))
