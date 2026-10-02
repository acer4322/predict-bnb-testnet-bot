from __future__ import annotations
import json,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
CF=[ROOT/'data/research/lan_worker_returns/r4-winrate-empty20-a-v1/conversion.json',ROOT/'data/research/lan_worker_returns/r4-winrate-empty20-b-v1/conversion.json']
PF=[ROOT/'data/research/lan_worker_returns/r4-winrate-empty20-path-a-v1/path.json',ROOT/'data/research/lan_worker_returns/r4-winrate-empty20-path-b-v1/path.json']
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_adaptive_winrate_empty_path_challenge20_score_v1.json'
def main():
 conv={};path={}
 for fp in CF:
  for r in json.loads(fp.read_text(encoding='utf-8'))['rows']:conv[int(r['marketId'])]=r
 for fp in PF:
  for r in json.loads(fp.read_text(encoding='utf-8'))['rows']:path[int(r['marketId'])]=r
 rows=[]
 for mid,c in conv.items():
  base=c.get('baseline') or {};cf=c.get('counterfactual'); pr=path.get(mid); ac=None
  if pr: ac=(((pr.get('pathState') or {}).get('summary') or {}).get('activeOrderCount'))
  approve=bool(cf is not None and ac==0)
  chosen=cf if approve else base
  rows.append({'marketId':mid,'activeOrderCount':ac,'approveRepair':approve,'baselinePnl':base.get('pnlUsdt'),'repairPnl':(cf or {}).get('pnlUsdt') if cf else None,'chosenPnl':chosen.get('pnlUsdt'),'baselineWin':bool((base.get('pnlUsdt') or 0)>0),'chosenWin':bool((chosen.get('pnlUsdt') or 0)>0),'conversion':c.get('conversion')})
 bw=sum(r['baselineWin'] for r in rows);cw=sum(r['chosenWin'] for r in rows);n=len(rows)
 appr=[r for r in rows if r['approveRepair']]
 rep={'version':'R4_ADAPTIVE_WINRATE_EMPTY_PATH_CHALLENGE20_SCORE_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'frozenContract':'r4_adaptive_winrate_empty_path_selector_challenge_v1.json','marketCount':n,'baselineWins':bw,'selectorWins':cw,'baselineWinRate':bw/n,'selectorWinRate':cw/n,'winRateDeltaPp':100*(cw-bw)/n,'baselineTotalPnl':sum(float(r['baselinePnl']) for r in rows),'selectorTotalPnl':sum(float(r['chosenPnl']) for r in rows),'approvedRepairs':len(appr),'approvedConversions':dict(Counter(r['conversion'] for r in appr)),'winnerDamageCount':sum(r['baselineWin'] and not r['chosenWin'] for r in rows),'lossToWinCount':sum((not r['baselineWin']) and r['chosenWin'] for r in rows),'rows':rows}
 rep['decision']='PASS_WINRATE_CHALLENGE' if rep['selectorWinRate']>rep['baselineWinRate'] and rep['winnerDamageCount']==0 else 'FAIL_WINRATE_CHALLENGE'
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
