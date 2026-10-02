from __future__ import annotations
import json, sqlite3
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_CROSS_GAP_EXCESS_VALUE_ROBUSTNESS_V1.json'
EPS=1e-9

def pct(a,p):
    a=np.sort(np.asarray(a,float));
    if len(a)==0:return None
    return float(np.quantile(a,p))

def main():
    con=sqlite3.connect(DB)
    winners={int(m):str(w).upper() for m,w in con.execute("select market_id,winner from target_markets where asset='ETH' and winner in ('UP','DOWN')")}
    mids=sorted(winners); test=set(mids[int(len(mids)*.8):])
    evs=defaultdict(list)
    for m,role,side,t,p,q,pid in con.execute("select market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where asset='ETH' and average_price is not null and shares>0 order by market_id,first_event_ms,parent_id"):
        m=int(m)
        if m in test and m in winners: evs[m].append((str(role),str(side).upper(),float(p),float(q)))
    con.close()
    per={'MAKER':defaultdict(lambda:[0.0,0.0]),'TAKER':defaultdict(lambda:[0.0,0.0])}
    for m,xs in evs.items():
        U=D=0.0;win=winners[m]
        for role,side,p,q in xs:
            if abs(U-D)>EPS:
                weak='UP' if U<D else 'DOWN'; gap=abs(U-D)
                if side==weak and q>gap+EPS and role in per:
                    ex=q-gap; val=ex*((1-p) if side==win else -p)
                    per[role][m][0]+=ex; per[role][m][1]+=val
            if side=='UP':U+=q
            else:D+=q
    rng=np.random.default_rng(7);out={'version':'TARGET_ETH_CROSS_GAP_EXCESS_VALUE_ROBUSTNESS_V1','researchOnly':True,'winnerLeakage':'SCORING_ONLY','roles':{}}
    for role,mp in per.items():
        items=[(m,e,v) for m,(e,v) in mp.items() if e>EPS]; totalE=sum(e for _,e,_ in items);totalV=sum(v for _,_,v in items)
        ratios=[v/e for _,e,v in items];boots=[]
        if items:
            n=len(items)
            arr=np.asarray([[e,v] for _,e,v in items],float)
            for _ in range(1000):
                idx=rng.integers(0,n,size=n); sm=arr[idx].sum(axis=0); boots.append(sm[1]/sm[0] if sm[0]>EPS else 0)
        out['roles'][role]={'markets':len(items),'totalExcessShares':totalE,'totalValue':totalV,'totalValuePerShare':totalV/totalE if totalE>EPS else None,
            'medianMarketValuePerShare':float(np.median(ratios)) if ratios else None,'positiveValueMarketRate':sum(r>0 for r in ratios)/len(ratios) if ratios else None,
            'bootstrap1000ValuePerShare':{'p05':pct(boots,.05),'p50':pct(boots,.5),'p95':pct(boots,.95)}}
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
