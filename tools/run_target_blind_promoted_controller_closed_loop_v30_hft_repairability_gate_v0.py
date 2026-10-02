from __future__ import annotations
import importlib.util,sys,math,json,joblib,os
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; TOOLS=ROOT/'tools'
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))
P=TOOLS/'run_target_blind_promoted_controller_closed_loop_v2_hft_early_side_redistribute_v1.py'
spec=importlib.util.spec_from_file_location('hftbase_rg',P); hft=importlib.util.module_from_spec(spec); sys.modules[spec.name]=hft; assert spec and spec.loader; spec.loader.exec_module(hft)
if os.environ.get('CTRL_OUR_DB'): hft.legacy.mod.DEFAULT_OUR_DB=Path(os.environ['CTRL_OUR_DB'])
MODE_GATE=os.environ.get('REPAIR_GATE_MODE','ON').upper()
ART=ROOT/'data/research/execution_aware_fill_lifecycle_v0/hft_native_residual_repairability_runtime_min_v0.joblib'; art=joblib.load(ART); model=art['model']; fs=list(art['features']); THR=float(art['thresholdTop40'])
base_add=hft.legacy.add_order; stats={'evaluated':0,'blocked':0,'allowedHighRepair':0,'notDominantGrowth':0,'threshold':THR,'mode':MODE_GATE}
def sv(s,*ks):
 for k in ks:
  v=s.get(k)
  if v is not None:
   try:return float(v)
   except Exception:pass
 return math.nan
def gated(sim,snapshot,book_state,side,ns,now,places,meta,market_id,p,reason,allow_stack=True,bypass_guard=False):
 u,d,g,net,pc=hft.legacy.maker_totals(sim); dom='UP' if net>1e-9 else 'DOWN' if net<-1e-9 else None
 if MODE_GATE=='ON' and (not bypass_guard) and str(reason)=='MAKER_HAZARD' and abs(net)>=18-1e-9 and dom==str(side):
  stats['evaluated']+=1
  bid=sv(snapshot,'predictUpBid','predict_up_bid') if side=='UP' else sv(snapshot,'predictDownBid','predict_down_bid')
  ask=sv(snapshot,'predictUpAsk','predict_up_ask') if side=='UP' else sv(snapshot,'predictDownAsk','predict_down_ask')
  spread=(ask-bid)/0.01 if math.isfinite(bid) and math.isfinite(ask) else math.nan
  raw={'maker_net':net,'maker_abs_net':abs(net),'maker_imbalance_ratio':abs(net)/g if g>1e-9 else 0.0,'maker_paired_coverage':pc,'current_bid':bid,'current_ask':ask,'current_spread_ticks':spread}
  x=pd.DataFrame([{f:raw.get(f,math.nan) for f in fs}]); pr=float(model.predict_proba(x)[0,1])
  if pr<THR:
   stats['blocked']+=1; return False
  stats['allowedHighRepair']+=1
 else: stats['notDominantGrowth']+=1
 return base_add(sim,snapshot,book_state,side,ns,now,places,meta,market_id,p,reason,allow_stack,bypass_guard)
hft.legacy.add_order=gated
suf=os.environ.get('CTRL_SUFFIX',''); hft.legacy.PREFIX=hft.legacy.OUT/f'target_blind_promoted_controller_closed_loop_v30_hft_repairability_gate_v0{suf}'; hft.legacy.REPORT=Path(str(hft.legacy.PREFIX)+'_report.json');hft.legacy.MARKETS=Path(str(hft.legacy.PREFIX)+'_markets.csv');hft.legacy.ACTIONS=Path(str(hft.legacy.PREFIX)+'_actions.csv');hft.legacy.STATES=Path(str(hft.legacy.PREFIX)+'_states.csv')
if __name__=='__main__':
 rc=1
 try:
  rc=hft.legacy.main()
  if hft.legacy.REPORT.exists():
   rep=json.loads(hft.legacy.REPORT.read_text()); rep['reportVersion']='HFT_REPAIRABILITY_GATE_V0'; rep['repairabilityGate']=stats; rep['hftConfig']={'dreamFillAllowed':False,'entryLatencyMs':hft.ENTRY_LATENCY_MS,'responseLatencyMs':hft.RESPONSE_LATENCY_MS,'queueModel':hft.QUEUE_MODEL}; hft.legacy.REPORT.write_text(json.dumps(rep,indent=2,allow_nan=True))
 finally:
  for v in hft.venues.values(): v.close()
 raise SystemExit(rc)
