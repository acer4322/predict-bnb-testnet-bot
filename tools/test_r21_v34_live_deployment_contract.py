from __future__ import annotations
import ast, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ctrl=(ROOT/'src/predict_bot/unified_controller_r2_r21_echtgeld_v1.py').read_text(encoding='utf-8')
bridge=(ROOT/'src/predict_bot/r21_echtgeld_state_bridge_v1.py').read_text(encoding='utf-8')
engine=(ROOT/'src/predict_bot/echtgeld_engine_v23.py').read_text(encoding='utf-8')
launcher=(ROOT/'start-unified-controller-r2-r21-echtgeld-v1.ps1').read_text(encoding='utf-8')
for name,text in [('controller',ctrl),('bridge',bridge),('engine',engine)]: ast.parse(text,filename=name)
checks={
 'exact_10_shares':'R2_R21_LIVE_SHARES = 10.0' in ctrl,
 'notional_cap_disabled':'R2_R21_NOTIONAL_CAP_ENABLED = False' in ctrl and 'R2_R21_10SHARE_NO_NOTIONAL_CAP' in ctrl,
 'canonical_frozen_r2_base':'unified_controller_paper_v2 as base' in ctrl and 'UnifiedControllerPaperV2' in ctrl,
 'v33_transport_preserved':'stateSequenceSummary' in bridge and 'R21_ECHTGELD_STATE_BRIDGE_V33_CONTEXT_SEQUENCE' in bridge,
 'r21_no_action_authority':'"actionAuthority": False' in bridge and '"executorCallbackAllowed": False' in bridge,
 'v34_ranker_loaded':'R2_MULTICHILD_PAIRWISE_RANKER_V3' in ctrl and 'READ_ONLY_RELATIVE_RANKING' in ctrl,
 'ranker_no_cancel_authority':'"absoluteRetireThreshold": None' in ctrl and '"cancelAuthority": False' in ctrl,
 'ranker_price_projected':'requestedPrice' in bridge and 'requested_price' in engine,
 'venue_owner_8781':'"executionOwner": "8781_ONLY"' in ctrl,
 'launcher_v34':'UNIFIED_R2_R21_V34_MULTICHILD_RANKER_ECHTGELD_10SHARE_V1' in launcher,
 'engine_strategy_v34':'R2_R21_V34_MULTICHILD_RANKER_10SHARE_NO_NOTIONAL_CAP' in engine,
 'taker_timeout_cancel_owned_by_engine':'R2_R21_TAKER_CONFIRM_TIMEOUT_MS = 2_200' in engine and '_cancel_expired_r2_r21_takers' in engine,
 'taker_timeout_retains_terminal_ownership':'r2R21TakerTimeoutCancelWaitsTerminal' in engine and 'lifecyclePollingIndependentOfMarketSnapshots' in ctrl,
}
rep={'version':'R21_V34_LIVE_DEPLOYMENT_CONTRACT_V1','checks':checks,'pass':all(checks.values()),'configuredShares':10.0,'notionalCapEnabled':False,'r21ActionAuthority':False,'rankerCancelAuthority':False,'executionOwner':'8781_ONLY','takerConfirmTimeoutMs':2200}
out=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r21_v34_live_deployment_contract_v1_report.json';out.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if not rep['pass']: raise SystemExit(1)
