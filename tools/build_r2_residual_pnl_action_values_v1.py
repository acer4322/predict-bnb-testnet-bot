from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.build_r2_residual_intervention_curriculum_v0 import run_recovery
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SPLITS={
 'train':'r2_residual_multicheckpoint_train15_v0.json',
 'validation':'r2_residual_multicheckpoint_validation15_v0.json',
 'forward':'r2_residual_multicheckpoint_forward20_v0.json'}
for split,fn in SPLITS.items():
 src=json.loads((D/fn).read_text())
 rows=[]
 for i,r in enumerate(src['rowsData'],1):
  mid=int(r['marketId']); delay=int(r['candidateDelayMs'])
  # Existing curriculum already stores passive-priority delta PnL vs baseline.
  b=run_recovery(mid,enable_intervention=False,candidate_delay_ms=delay,passive_priority=False)
  t=run_recovery(mid,enable_intervention=True,candidate_delay_ms=delay,passive_priority=False)
  bp=b.get('realizedPnl'); tp=t.get('realizedPnl')
  if bp is None or tp is None: continue
  passive=float(bp)+float(r.get('deltaPnlDiagnostic') or 0.0)
  vals={'BASELINE_R2':float(bp),'PASSIVE_PRIORITY':passive,'TAKER_RECOVERY':float(tp)}
  best=max(vals,key=vals.get)
  rows.append({
   'marketId':mid,'candidateDelayMs':delay,'episodeAtMs':r.get('episodeAtMs'),
   'features':r.get('features') or {},'actionPnl':vals,'bestAction':best,
   'bestPnl':vals[best],'baselinePnl':float(bp),'bestDeltaPnl':vals[best]-float(bp)})
  print(json.dumps({'split':split,'progress':i,'rows':len(src['rowsData']),'marketId':mid,'delay':delay,'best':best,'delta':vals[best]-float(bp)},ensure_ascii=False),flush=True)
 rep={'version':'R2_RESIDUAL_PNL_ACTION_VALUES_V1','researchOnly':True,'dreamFillAllowed':False,
      'split':split,'source':fn,'primaryObjective':'final executable market PnL',
      'actionFamilies':['BASELINE_R2','PASSIVE_PRIORITY','TAKER_RECOVERY'],
      'fixedDelaysMs':[0,1000,3000,5000,10000],
      'guardrails':['Existing HftBacktest execution physics only','Existing preregistered delay grid only','No Target/winner/PnL in runtime features','No threshold sweep'],
      'rows':rows}
 out=D/f'r2_residual_pnl_action_values_v1_{split}.json'; out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 print(json.dumps({'done':split,'out':str(out),'rows':len(rows)},ensure_ascii=False),flush=True)
