from __future__ import annotations
import argparse,json,sys,warnings,sqlite3
warnings.filterwarnings('ignore',message='X does not have valid feature names')
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_small_deficit_abstention_v2 as guard
P=ROOT/'data/research/r4_v0/p0_provenance_v1'

def settlement(mid:int):
 c=sqlite3.connect(ROOT/'data/target_wallet_official_v1.db')
 r=c.execute("select winner,resolved_at_ms from target_market_results where market_id=? and asset='BTC'",(int(mid),)).fetchone(); c.close()
 if not r or str(r[0]) not in {'UP','DOWN'}: raise RuntimeError(f'no settlement {mid}')
 return str(r[0]),int(r[1])

def score(rep,winner):
 maker=rep.get('makerFillEvents') or []; taker=rep.get('takerEvents') or []
 up=sum(float(x.get('deltaShares') or x.get('shares') or 0) for x in maker if str(x.get('side'))=='UP')+sum(float(x.get('shares') or x.get('filledShares') or 0) for x in taker if str(x.get('side'))=='UP')
 dn=sum(float(x.get('deltaShares') or x.get('shares') or 0) for x in maker if str(x.get('side'))=='DOWN')+sum(float(x.get('shares') or x.get('filledShares') or 0) for x in taker if str(x.get('side'))=='DOWN')
 sr=rep.get('studentRollout') or {}; cost=float(sr.get('makerCostUsdt') or 0)+float(sr.get('takerCostUsdt') or 0)+float(sr.get('takerFeesUsdt') or 0)
 payout=up if winner=='UP' else dn; pnl=payout-cost; pf=(sr.get('finalPortfolio') or {})
 return {'winner':winner,'upShares':up,'downShares':dn,'costUsdt':cost,'payoutUsdt':payout,'pnlUsdt':pnl,'positive':bool(pnl>0),'makerFillEvents':int(sr.get('makerFillEvents') or 0),'makerFilledShares':float(sr.get('makerFilledShares') or 0),'takerFills':int(sr.get('takerFills') or 0),'takerFilledShares':float(sr.get('takerFilledShares') or 0),'finalFloor':float(pf.get('worst_case_floor') or 0),'finalAbsNet':float(pf.get('combined_abs_net') or 0)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--start',type=int,default=0);ap.add_argument('--count',type=int,default=999);ap.add_argument('--tag',default='dev');a=ap.parse_args();ids=[int(x) for x in json.loads((ROOT/a.ids_json).read_text())][a.start:a.start+a.count];rows=[];events=[]
 for mid in ids:
  try:
   rep=guard.run_overlay(mid,True,events);w,res=settlement(mid);s=score(rep,w);rows.append({'marketId':mid,'resolvedAtMs':res,'score':s,'mgmtStats':rep.get('mgmtStats') or {}});print(json.dumps({'marketId':mid,'pnl':s['pnlUsdt'],'stats':rep.get('mgmtStats') or {}},ensure_ascii=False),flush=True)
  except Exception as ex: rows.append({'marketId':mid,'error':f'{type(ex).__name__}:{ex}'});print(json.dumps(rows[-1]),flush=True)
 out=P/f'r4_small_deficit_abstention_v2_{a.tag}_chunk_{a.start}_{len(ids)}.json';out.write_text(json.dumps({'version':'R4_SMALL_DEFICIT_ABSTENTION_V2_SCORE_CHUNK','researchOnly':True,'tag':a.tag,'rows':rows,'events':events},indent=2,ensure_ascii=False,allow_nan=True));print(out)
if __name__=='__main__':main()
