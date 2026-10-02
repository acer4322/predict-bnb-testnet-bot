from __future__ import annotations
import json, math, sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.recoverability import RecursiveCompositeCurrentCoordinateRecoverabilityPolicy as Policy, RecursiveCompositeRecoverabilityContext as Ctx
EPS=1e-9
p=Policy()
cases=[
 ('dev4',Ctx(-0.3690787561755302,-0.8420373952288847,'DOWN',1.25867292476764,0.41,0.56,4,12),True,4),
 ('dev2_horizon_control',Ctx(-0.3690787561755302,-0.8420373952288847,'DOWN',1.25867292476764,0.41,0.56,2,12),False,None),
 ('two_carrier_support',Ctx(-0.3690787561755302,-0.8420373952288847,'DOWN',1.25867292476764,0.40,0.56,4,12),True,2),
 ('adverse_pair_control',Ctx(-0.3690787561755302,-0.8420373952288847,'DOWN',1.25867292476764,0.52,0.55,4,12),False,None),
]
rows=[]; all_cons=True; all_venue=True; expectations=True
for name,ctx,exp,step in cases:
 z=p.evaluate(ctx); cons=all(abs(float(x['physicalQty'])-float(x['repairAllocation'])-float(x['overflowAllocation']))<=1e-9 for x in z.path); venue=all(float(x['physicalQty'])<=ctx.max_venue_qty+EPS for x in z.path)
 all_cons &= cons; all_venue &= venue; expectations &= (z.recoverable==exp and (step is None or z.recovered_step==step))
 rows.append({'case':name,'expectedRecoverable':exp,'decision':{'recoverable':z.recoverable,'reason':z.reason,'recoveredStep':z.recovered_step,'terminalFloor':z.terminal_floor,'terminalDebt':z.terminal_debt,'maxDebt':z.max_debt,'pairSum':z.pair_sum},'path':list(z.path),'conservation':cons,'venueBound':venue})
out={'version':'RECURSIVE_COMPOSITE_RECOVERABILITY_POLICY_MICROWORLD_RESULT','date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,'gates':{'expectationsMatch':expectations,'physicalConservationExact':all_cons,'venueBoundRespected':all_venue,'boundedTermination':all(len(r['path'])<=4 for r in rows),'positiveAndNegativeDiscrimination':any(r['decision']['recoverable'] for r in rows) and any(not r['decision']['recoverable'] for r in rows)},'rows':rows}
out['decision']='PASS_TO_ONE_MARKET_BEHAVIOR_SMOKE' if all(out['gates'].values()) else 'REJECT_OR_DIAGNOSE_POLICY'
q=Path('data/research/r4_v0/behavior_alignment_v1/RECURSIVE_COMPOSITE_RECOVERABILITY_POLICY_MICROWORLD_RESULT_20260904.json');q.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'decision':out['decision'],'gates':out['gates'],'rows':[{'case':r['case'],'recoverable':r['decision']['recoverable'],'step':r['decision']['recoveredStep'],'maxDebt':r['decision']['maxDebt']} for r in rows]},ensure_ascii=False,indent=2))
