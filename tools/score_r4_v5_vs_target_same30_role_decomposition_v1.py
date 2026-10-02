from __future__ import annotations
import json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools import test_r4_completion_horizon_admission_v5 as v5
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FORMAL=[1783080,1783072,1783069,1782821,1782477,1782469,1782465,1782273,1782254,1782044]
EXT=[1783154,1783150,1782750,1782487,1782453,1782437,1782263,1782260,1777712,1777237,1777186,1777167,1777166,1776637,1775470,1775465,1774858,1774549,1774144,1770676]
MIDS=FORMAL+EXT

def settlement(mid):
 c=sqlite3.connect(ROOT/'data/target_wallet_official_v1.db');c.row_factory=sqlite3.Row
 r=c.execute("select winner,net_pnl_usdt,maker_net_pnl_usdt,taker_net_pnl_usdt from target_market_results where asset='BTC' and market_id=?",(mid,)).fetchone();c.close()
 if not r: raise RuntimeError(f'no settlement {mid}')
 return dict(r)

def role_score(rep,winner):
 maker=rep.get('makerFillEvents') or []; taker=rep.get('takerEvents') or []
 maker_win=sum(float(x.get('deltaShares') or 0) for x in maker if str(x.get('side'))==winner)
 taker_win=sum(float(x.get('shares') or 0) for x in taker if str(x.get('side'))==winner)
 sr=rep['studentRollout']; maker_cost=float(sr.get('makerCostUsdt') or 0); taker_cost=float(sr.get('takerCostUsdt') or 0); fees=float(sr.get('takerFeesUsdt') or 0)
 return {'makerPnlUsdt':maker_win-maker_cost,'takerPnlUsdt':taker_win-taker_cost-fees,'totalPnlUsdt':maker_win+taker_win-maker_cost-taker_cost-fees,'makerWinningShares':maker_win,'takerWinningShares':taker_win,'makerFillEvents':len(maker),'makerFilledShares':sum(float(x.get('deltaShares') or 0) for x in maker),'takerFills':len(taker),'takerFilledShares':sum(float(x.get('shares') or 0) for x in taker),'finalFloor':float((sr.get('finalPortfolio') or {}).get('worst_case_floor') or 0),'finalAbsNet':float((sr.get('finalPortfolio') or {}).get('combined_abs_net') or 0)}

def main():
 rows=[]; tev=[]
 for mid in MIDS:
  st=settlement(mid); w=str(st['winner'])
  r3=r3ctl.run_market(mid,True); r4=v5.run_overlay(mid,True,tev)
  a=role_score(r3,w); b=role_score(r4,w)
  rows.append({'marketId':mid,'winner':w,'target':{'totalPnlUsdt':float(st['net_pnl_usdt']),'makerPnlUsdt':float(st['maker_net_pnl_usdt']),'takerPnlUsdt':float(st['taker_net_pnl_usdt'])},'R3':a,'V5':b})
  print(mid,round(a['totalPnlUsdt'],4),round(b['totalPnlUsdt'],4),round(float(st['net_pnl_usdt']),4),flush=True)
 def agg(key):
  rr=[x[key] for x in rows]; return {'markets':len(rr),'wins':sum(float(x['totalPnlUsdt'])>0 for x in rr),'totalPnlUsdt':sum(float(x['totalPnlUsdt']) for x in rr),'makerPnlUsdt':sum(float(x['makerPnlUsdt']) for x in rr),'takerPnlUsdt':sum(float(x['takerPnlUsdt']) for x in rr),'makerFillEvents':sum(int(x.get('makerFillEvents',0)) for x in rr),'makerFilledShares':sum(float(x.get('makerFilledShares',0)) for x in rr),'takerFills':sum(int(x.get('takerFills',0)) for x in rr),'takerFilledShares':sum(float(x.get('takerFilledShares',0)) for x in rr),'meanFinalFloor':sum(float(x.get('finalFloor',0)) for x in rr)/len(rr),'meanFinalAbsNet':sum(float(x.get('finalAbsNet',0)) for x in rr)/len(rr)}
 out={'version':'R4_V5_VS_TARGET_SAME30_ROLE_DECOMPOSITION_V1','researchOnly':True,'cohort':MIDS,'R3':agg('R3'),'V5':agg('V5'),'target':{'markets':len(rows),'wins':sum(x['target']['totalPnlUsdt']>0 for x in rows),'totalPnlUsdt':sum(x['target']['totalPnlUsdt'] for x in rows),'makerPnlUsdt':sum(x['target']['makerPnlUsdt'] for x in rows),'takerPnlUsdt':sum(x['target']['takerPnlUsdt'] for x in rows)},'rows':rows}
 (P/'r4_v5_vs_target_same30_role_decomposition_v1.json').write_text(json.dumps(out,indent=2,allow_nan=True),encoding='utf-8')
 print(json.dumps(out['R3'],indent=2));print(json.dumps(out['V5'],indent=2));print(json.dumps(out['target'],indent=2))
if __name__=='__main__': main()
