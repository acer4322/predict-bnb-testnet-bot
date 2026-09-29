from __future__ import annotations
import argparse,json,sqlite3,sys,warnings
warnings.filterwarnings("ignore", message="X does not have valid feature names")
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools import hftbacktest_r3_r31_maker10_adapter_v1 as mk10
from tools import compare_r3_vs_r4_management_exact_v8_enriched as r4exact
from tools.compare_r3_vs_r4_management_control_v1 import summary as safety_summary
P=ROOT/'data/research/r4_v0/p0_provenance_v1';IDS=P/'r4_threeway10_benchmark_ids_v1.json'

def settlement(mid:int):
 c=sqlite3.connect(ROOT/'data/target_wallet_official_v1.db')
 r=c.execute("select winner,resolved_at_ms from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();c.close()
 if not r or str(r[0]) not in {'UP','DOWN'}:raise RuntimeError(f'no settlement {mid}')
 return str(r[0]),int(r[1])

def score(rep,winner):
 maker=rep.get('makerFillEvents') or [];taker=rep.get('takerEvents') or []
 up=sum(float(x.get('deltaShares') or x.get('shares') or 0) for x in maker if str(x.get('side'))=='UP')+sum(float(x.get('shares') or x.get('filledShares') or 0) for x in taker if str(x.get('side'))=='UP')
 dn=sum(float(x.get('deltaShares') or x.get('shares') or 0) for x in maker if str(x.get('side'))=='DOWN')+sum(float(x.get('shares') or x.get('filledShares') or 0) for x in taker if str(x.get('side'))=='DOWN')
 sr=rep.get('studentRollout') or {};cost=float(sr.get('makerCostUsdt') or 0)+float(sr.get('takerCostUsdt') or 0)+float(sr.get('takerFeesUsdt') or 0)
 payout=up if winner=='UP' else dn;pnl=payout-cost;pf=(sr.get('finalPortfolio') or {})
 return {'winner':winner,'upShares':up,'downShares':dn,'costUsdt':cost,'payoutUsdt':payout,'pnlUsdt':pnl,'positive':bool(pnl>0),'makerFillEvents':int(sr.get('makerFillEvents') or 0),'makerFilledShares':float(sr.get('makerFilledShares') or 0),'takerFills':int(sr.get('takerFills') or 0),'takerFilledShares':float(sr.get('takerFilledShares') or 0),'finalFloor':float(pf.get('worst_case_floor') or 0),'finalAbsNet':float(pf.get('combined_abs_net') or 0)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,default=0);ap.add_argument('--count',type=int,default=2);a=ap.parse_args();ids=json.loads(IDS.read_text());sub=[int(x) for x in ids[a.start:a.start+a.count]];rows=[];events=[]
 for mid in sub:
  rec={'marketId':mid}
  try:
   r3=r3ctl.run_market(mid,True)
   m10,a10=mk10.run_market(mid)
   off=r4exact.run_overlay(mid,False,[])
   eq=safety_summary(r3)==safety_summary(off);rec['r4SeedEquivalent']=bool(eq);rec['seedReferenceR3']=safety_summary(r3);rec['seedReferenceOff']=safety_summary(off)
   r4=r4exact.run_overlay(mid,True,events) if eq else None
   # Settlement is intentionally read only after all requested rollouts are complete.
   w,resolved=settlement(mid);rec['resolvedAtMs']=resolved;rec['R3_R31_FULL_RESEARCH']=score(r3,w);rec['R3_R31_MAKER10']=score(m10,w);rec['maker10Audit']={k:a10.get(k) for k in ['allMakerQty10','takerSizingMode','takerDynamicNon10Observed','makerQtyUnique','takerRequestedQtyUnique']}
   if r4 is not None:rec['R4_MANAGEMENT_TESTBED']=score(r4,w);rec['r4MgmtStats']=r4.get('mgmtStats') or {}
   else:rec['R4_MANAGEMENT_TESTBED_ERROR']='SEED_EQUIVALENCE_FAIL'
  except Exception as ex:rec['error']=f'{type(ex).__name__}:{ex}'
  rows.append(rec);print(json.dumps({'marketId':mid,'seedEq':rec.get('r4SeedEquivalent'),'r3Pnl':(rec.get('R3_R31_FULL_RESEARCH') or {}).get('pnlUsdt'),'m10Pnl':(rec.get('R3_R31_MAKER10') or {}).get('pnlUsdt'),'r4Pnl':(rec.get('R4_MANAGEMENT_TESTBED') or {}).get('pnlUsdt'),'error':rec.get('error')},ensure_ascii=False),flush=True)
 out=P/f'r4_threeway10_benchmark_chunk_{a.start}_{len(sub)}_v1.json';out.write_text(json.dumps({'version':'R4_THREEWAY10_BENCHMARK_CHUNK_V1','researchOnly':True,'contract':'r4_threeway10_action_enabled_benchmark_contract_v1.json','start':a.start,'rows':rows,'r4Events':events},indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'artifact':str(out.relative_to(ROOT)),'rows':len(rows),'errors':sum('error' in r for r in rows),'seedFailures':sum(not r.get('r4SeedEquivalent',False) for r in rows)},ensure_ascii=False))
if __name__=='__main__':main()
