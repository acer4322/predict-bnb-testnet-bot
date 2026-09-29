from __future__ import annotations
import argparse,json,sqlite3,sys,warnings
from pathlib import Path
warnings.filterwarnings('ignore',message='X does not have valid feature names')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_completion_horizon_admission_v5 as guard
P=ROOT/'data/research/r4_v0/p0_provenance_v1'

def settlement(mid:int):
    c=sqlite3.connect(ROOT/'data/target_wallet_official_v1.db'); r=c.execute("select winner,resolved_at_ms from target_market_results where market_id=? and asset='BTC'",(int(mid),)).fetchone(); c.close()
    if not r: raise RuntimeError(f'no settlement {mid}')
    return str(r[0]),int(r[1])

def score(rep,winner):
    maker=rep.get('makerFillEvents') or []; taker=rep.get('takerEvents') or []
    up=sum(float(x.get('deltaShares') or x.get('shares') or 0) for x in maker if str(x.get('side'))=='UP')+sum(float(x.get('shares') or 0) for x in taker if str(x.get('side'))=='UP')
    dn=sum(float(x.get('deltaShares') or x.get('shares') or 0) for x in maker if str(x.get('side'))=='DOWN')+sum(float(x.get('shares') or 0) for x in taker if str(x.get('side'))=='DOWN')
    sr=rep['studentRollout']; cost=float(sr.get('makerCostUsdt') or 0)+float(sr.get('takerCostUsdt') or 0)+float(sr.get('takerFeesUsdt') or 0); pnl=(up if winner=='UP' else dn)-cost; pf=sr.get('finalPortfolio') or {}
    return {'pnlUsdt':pnl,'positive':pnl>0,'upShares':up,'downShares':dn,'finalFloor':float(pf.get('worst_case_floor') or 0),'finalAbsNet':float(pf.get('combined_abs_net') or 0)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--ids-json',required=True); ap.add_argument('--tag',required=True); a=ap.parse_args()
    ids=[int(x) for x in json.loads((ROOT/a.ids_json).read_text(encoding='utf-8'))]; rows=[]; events=[]
    for mid in ids:
        try:
            r3rep=guard.r3ctl.run_market(mid,True); off=guard.run_overlay(mid,False,[]); A=guard.summary(r3rep); O=guard.summary(off); equiv=(A==O)
            w,res=settlement(mid); row={'marketId':mid,'resolvedAtMs':res,'winner':w,'seedEquivalent':equiv,'R3Summary':A,'overlayOffSummary':O,'R3Score':score(r3rep,w)}
            if equiv:
                on=guard.run_overlay(mid,True,events); row['V5Score']=score(on,w); row['mgmtStats']=on.get('mgmtStats') or {}; row['V5Summary']=guard.summary(on)
                row['deltaPnlUsdt']=row['V5Score']['pnlUsdt']-row['R3Score']['pnlUsdt']; row['deltaFloor']=row['V5Score']['finalFloor']-row['R3Score']['finalFloor']; row['deltaAbsNet']=row['V5Score']['finalAbsNet']-row['R3Score']['finalAbsNet']
            rows.append(row); print(mid,'equiv',equiv,'r3',round(row['R3Score']['pnlUsdt'],4),'v5',round(row.get('V5Score',{}).get('pnlUsdt',float('nan')),4),flush=True)
        except Exception as ex:
            rows.append({'marketId':mid,'error':f'{type(ex).__name__}:{ex}'}); print(rows[-1],flush=True)
    out=P/f'r4_completion_horizon_v5_{a.tag}.json'; out.write_text(json.dumps({'version':'R4_COMPLETION_HORIZON_V5_FORMAL_SCORE_V1','researchOnly':True,'tag':a.tag,'rows':rows,'events':events},indent=2,allow_nan=True),encoding='utf-8'); print(out)
if __name__=='__main__': main()
