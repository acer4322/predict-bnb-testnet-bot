from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data'/'research'/'our_postfill_arbitration_counterfactual_v0_states.jsonl'
REPORT=ROOT/'data'/'research'/'our_postfill_pair_edge_policy_v0_report.json'

rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
policies={
 'PAIR_EDGE_SIGN_SWITCH_ELSE_PAUSE': lambda r: 'SWITCH_OPPOSITE' if float(r['oppBestBidLockedEdge'])>=0 else 'PAUSE',
 'ALWAYS_SWITCH': lambda r:'SWITCH_OPPOSITE',
 'ALWAYS_PAUSE': lambda r:'PAUSE',
 'ALWAYS_CONTINUE': lambda r:'CONTINUE_SAME',
}

def summarize(name,fn):
 vals=[]; counts={}
 for r in rows:
  a=fn(r); counts[a]=counts.get(a,0)+1; x=r['actions'][a]
  vals.append(x)
 def s(k):
  z=[float(v[k]) for v in vals]
  return {'sum':sum(z),'mean':sum(z)/len(z),'min':min(z),'max':max(z)}
 return {'actionCounts':counts,'mtmDelta':s('mtmDelta'),'floorDelta':s('floorDelta'),'absNetDelta':s('absNetDelta'),'pairEdgeDelta':s('pairEdgeDelta')}

out={'reportVersion':'OUR_POSTFILL_PAIR_EDGE_POLICY_V0','researchOnly':True,'runtimeFeatures':['oppBestBidLockedEdge at +1s checkpoint'],'rule':'If current-fill + opposite public best bid gross complementary edge >=0, choose SWITCH_OPPOSITE for 4s; otherwise PAUSE for 4s; then return to frozen stable-directional. No parameter sweep.','coverage':{'states':len(rows),'markets':len({r['marketId'] for r in rows})},'policies':{k:summarize(k,v) for k,v in policies.items()},'guard':['Same public-future local paper counterfactual labels as OUR_POSTFILL_ARBITRATION_COUNTERFACTUAL_V0.','No Target event, winner, settlement, or future regime is a runtime input.','This is a local arbitration V0 only; not a Flash/live strategy promotion.']}
REPORT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
