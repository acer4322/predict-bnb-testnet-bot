from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.build_r2_residual_intervention_curriculum_v0 import run_recovery
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
ap=argparse.ArgumentParser(); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
want={int(x) for x in a.market_ids.split(',') if x.strip()}
src=json.loads((D/'r2_residual_multicheckpoint_forward20_v0.json').read_text())
rows=[]
for i,r in enumerate([z for z in src['rowsData'] if int(z['marketId']) in want],1):
 mid=int(r['marketId']); delay=int(r['candidateDelayMs'])
 b=run_recovery(mid,enable_intervention=False,candidate_delay_ms=delay,passive_priority=False)
 t=run_recovery(mid,enable_intervention=True,candidate_delay_ms=delay,passive_priority=False)
 bp=b.get('realizedPnl'); tp=t.get('realizedPnl')
 if bp is None or tp is None: continue
 passive=float(bp)+float(r.get('deltaPnlDiagnostic') or 0.0)
 vals={'BASELINE_R2':float(bp),'PASSIVE_PRIORITY':passive,'TAKER_RECOVERY':float(tp)}; best=max(vals,key=vals.get)
 rows.append({'marketId':mid,'candidateDelayMs':delay,'episodeAtMs':r.get('episodeAtMs'),'features':r.get('features') or {},'actionPnl':vals,'bestAction':best,'bestPnl':vals[best],'baselinePnl':float(bp),'bestDeltaPnl':vals[best]-float(bp)})
 print(json.dumps({'progress':i,'marketId':mid,'delay':delay,'best':best,'delta':vals[best]-float(bp)},ensure_ascii=False),flush=True)
rep={'version':'R2_RESIDUAL_PNL_ACTION_VALUES_V1_FORWARD_BATCH','researchOnly':True,'dreamFillAllowed':False,'marketIds':sorted(want),'rows':rows}
out=D/a.output; out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps({'done':str(out),'rows':len(rows)},ensure_ascii=False))
