from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.test_r4_management_simulator_objective_semantics_v5_compare import load,run_variant,BASE,DUR
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=[ROOT/f'data/research/lan_worker_returns/r4-objv5-dev104-{s}/{s}.json' for s in 'abcd']
VAL=[ROOT/f'data/research/lan_worker_returns/r4-objv5-sixth20-{s}/{s}.json' for s in 'abcd']
def main():
 dev,dreps=load(DEV);val,vreps=load(VAL);z=run_variant('DURATION_MEMORY',BASE+DUR,dev,val,vreps);markets={int(r['marketId']) for r in val};struct={'validationMarkets':len(markets),'validationRoots':len(val),'executionCoreExactAll':all(x.get('executionCoreExact') for rep in vreps for x in rep['markets']),'duplicateExecutionCredit':sum(int(rep.get('duplicateExecutionCredit') or 0) for rep in vreps)};checks={'validationMarkets':len(markets)>=16,'validationRoots':len(val)>=20,'executionCoreExactAll':struct['executionCoreExactAll'],'duplicateExecutionCredit':struct['duplicateExecutionCredit']==0,'jointMotifMacroAccuracy':z['meanJointMotifMacroAccuracy']>=.45,'floorDirectionAgreement':z['meanFloorDirectionAgreement']>=.60,'absNetDirectionAgreement':z['meanAbsNetDirectionAgreement']>=.60,'meanPortfolioNMAE':z['meanPortfolioNMAE']<=.45};rep={'version':'R4_MANAGEMENT_SIMULATOR_OBJECTIVE_SEMANTICS_V5_FINAL_SIXTH20','researchOnly':True,'winner':'DURATION_MEMORY','winnerFreeze':'data/research/r4_v0/p0_provenance_v1/r4_management_simulator_objective_semantics_v5_winner_freeze.json','developmentRoots':len(dev),'validationMarkets':len(markets),'validationRoots':len(val),'metrics':z,'structural':struct,'checks':checks,'gatePass':all(checks.values()),'interpretation':'Final untouched SIXTH20 validation of the frozen DURATION_MEMORY variant. No SIXTH20 fitting or threshold sweep. Economics remain deterministic-ledger outputs.'};(P/'r4_management_simulator_objective_semantics_v5_final_sixth20.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
