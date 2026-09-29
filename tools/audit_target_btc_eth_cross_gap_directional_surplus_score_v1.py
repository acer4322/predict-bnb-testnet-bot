from __future__ import annotations
import json, sqlite3
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_ETH_CROSS_GAP_DIRECTIONAL_SURPLUS_SCORE_V1.json'
EPS=1e-9

def summarize(rows):
    cross=[r for r in rows if r['cross']]; non=[r for r in rows if not r['cross']]
    def wr(g): return sum(r['sideWin'] for r in g)/len(g) if g else None
    ex=sum(r['excess'] for r in cross); winex=sum(r['excess'] for r in cross if r['sideWin'])
    value=sum(r['excess']*((1-r['price']) if r['sideWin'] else -r['price']) for r in cross)
    bins=[]
    for name,lo,hi in [('LE025',0,.25),('025_050',.25,.5),('050_075',.5,.75),('GT075',.75,1.000001)]:
        g=[r for r in cross if r['price']<=hi and (r['price']>lo if lo>0 else True)]
        gx=sum(r['excess'] for r in g); gv=sum(r['excess']*((1-r['price']) if r['sideWin'] else -r['price']) for r in g)
        bins.append({'bin':name,'n':len(g),'winnerRate':wr(g),'excessShares':gx,'excessWeightedWinnerRate':sum(r['excess'] for r in g if r['sideWin'])/gx if gx>EPS else None,'excessValueTotal':gv,'excessValuePerShare':gv/gx if gx>EPS else None})
    return {'n':len(rows),'crossN':len(cross),'crossRate':len(cross)/len(rows) if rows else None,
            'candidateWinnerRateCross':wr(cross),'candidateWinnerRateNonCross':wr(non),
            'crossExcessShares':ex,'crossExcessWeightedWinnerRate':winex/ex if ex>EPS else None,
            'crossExcessValueTotal':value,'crossExcessValuePerShare':value/ex if ex>EPS else None,
            'crossPriceBins':bins}

def main():
    con=sqlite3.connect(DB)
    winners={}
    for a,m,w in con.execute("select asset,market_id,winner from target_markets where winner in ('UP','DOWN')"):
        winners[(a,int(m))]=str(w).upper()
    evs=defaultdict(list)
    for a,m,role,side,t,p,q,pid in con.execute('''select asset,market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where average_price is not null and shares>0 order by asset,market_id,first_event_ms,parent_id'''):
        if (a,int(m)) in winners: evs[(a,int(m))].append((str(role),str(side).upper(),int(t),float(p),float(q),pid))
    con.close()
    mids=defaultdict(list)
    for a,m in evs:mids[a].append(m)
    split={}
    for a,ms in mids.items():
        xs=sorted(set(ms));n=len(xs)
        for i,m in enumerate(xs):split[(a,m)]='TRAIN60' if (i+1)/n<=.6 else ('VALID20' if (i+1)/n<=.8 else 'TEST20')
    rows=[]
    for (a,m),xs in evs.items():
        U=D=C=0.0; winner=winners[(a,m)]
        for role,side,t,p,q,pid in xs:
            if abs(U-D)>EPS:
                weak='UP' if U<D else 'DOWN'
                if side==weak:
                    gap=abs(U-D);ex=max(0,q-gap);cross=q>gap+EPS
                    rows.append({'asset':a,'marketId':m,'role':role,'split':split[(a,m)],'side':side,'winner':winner,'sideWin':side==winner,'price':p,'qty':q,'gap':gap,'excess':ex,'cross':cross})
            if side=='UP':U+=q
            else:D+=q
            C+=p*q
    out={'version':'TARGET_BTC_ETH_CROSS_GAP_DIRECTIONAL_SURPLUS_SCORE_V1','researchOnly':True,'actionAuthority':False,'winnerLeakage':'SCORING_ONLY','coverage':{'rows':len(rows)},'groups':{}}
    for a in ('BTC','ETH'):
        for role in ('MAKER','TAKER'):
            g=[r for r in rows if r['asset']==a and r['role']==role]
            out['groups'][f'{a}_{role}']={'ALL':summarize(g),'TEST20':summarize([r for r in g if r['split']=='TEST20'])}
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'test20':{k:v['TEST20'] for k,v in out['groups'].items()}},indent=2))
if __name__=='__main__':main()
