from __future__ import annotations
import json, math, sqlite3
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_ETH_REEXPAND_GLOBAL_RESERVE_V41.json'
EPS=1e-9

def pct(a,p):
    x=sorted(float(v) for v in a if v is not None and math.isfinite(float(v)))
    if not x:return None
    z=(len(x)-1)*p;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
    return x[lo] if lo==hi else x[lo]*(1-w)+x[hi]*w

def st(a):
    x=[float(v) for v in a if v is not None and math.isfinite(float(v))]
    return {'n':len(x),'mean':sum(x)/len(x) if x else None,'median':pct(x,.5),'p25':pct(x,.25),'p75':pct(x,.75),'p90':pct(x,.9),'min':min(x) if x else None,'max':max(x) if x else None}

def summarize(rows):
    n=len(rows)
    if not n:return {'n':0}
    return {
        'n':n,'markets':len(set(r['marketId'] for r in rows)),
        'preFloorPositiveRate':sum(r['preFloor']>EPS for r in rows)/n,
        'preFloorNonnegativeRate':sum(r['preFloor']>=-EPS for r in rows)/n,
        'postFloorPositiveRate':sum(r['postFloor']>EPS for r in rows)/n,
        'postFloorNonnegativeRate':sum(r['postFloor']>=-EPS for r in rows)/n,
        'floorWorsenedRate':sum(r['postFloor']<r['preFloor']-EPS for r in rows)/n,
        'preFloor':st([r['preFloor'] for r in rows]),'postFloor':st([r['postFloor'] for r in rows]),
        'preBest':st([r['preBest'] for r in rows]),'preReserveRatio':st([r['preReserveRatio'] for r in rows]),
        'debtRemaining':st([r['debtPre'] for r in rows]),'repairProgressFrac':st([r['repairProgressFrac'] for r in rows]),
        'expandQty':st([r['qty'] for r in rows]),'expandNotional':st([r['qty']*r['price'] for r in rows])
    }

def main():
    con=sqlite3.connect(DB)
    evs=defaultdict(list)
    q='''select asset,market_id,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where role='MAKER' and average_price is not null and shares>0 order by asset,market_id,first_event_ms,parent_id'''
    for a,m,side,t,p,qty,pid in con.execute(q):
        evs[(str(a),int(m))].append((str(side).upper(),int(t),float(p),float(qty),pid))
    con.close()
    rows=[]
    for (asset,mid),xs in evs.items():
        U=D=C=0.0; debt=0.0; episode_initial=0.0; episode_id=0; first_reexpand_seen=False
        for side,t,p,qty,pid in xs:
            preU,preD,preC=U,D,C; preGap=abs(U-D); preFloor=min(U,D)-C; preBest=max(U,D)-C
            if side=='UP':U+=qty
            elif side=='DOWN':D+=qty
            else:continue
            C+=p*qty
            postGap=abs(U-D); postFloor=min(U,D)-C
            delta=postGap-preGap
            if delta>EPS:
                if debt<=EPS:
                    debt=delta;episode_initial=delta;episode_id+=1;first_reexpand_seen=False
                else:
                    prog=1.0-(debt/episode_initial) if episode_initial>EPS else 0.0
                    reserve_ratio=preFloor/preBest if preBest>EPS else None
                    rows.append({'asset':asset,'marketId':mid,'episodeId':episode_id,'firstReexpand':not first_reexpand_seen,'t':t,'side':side,'price':p,'qty':qty,
                                 'preFloor':preFloor,'postFloor':postFloor,'preBest':preBest,'preReserveRatio':reserve_ratio,'debtPre':debt,'episodeInitialDebt':episode_initial,'repairProgressFrac':max(0.0,min(1.0,prog))})
                    first_reexpand_seen=True;debt+=delta
            elif delta<-EPS and debt>EPS:
                debt=max(0.0,debt+delta)
                if debt<=EPS:
                    debt=0.0;episode_initial=0.0;first_reexpand_seen=False
    out={'version':'TARGET_BTC_ETH_REEXPAND_GLOBAL_RESERVE_V41','researchOnly':True,'actionAuthority':False,
         'definition':{'scope':'Target MAKER actual-filled parents only','reexpand':'abs-net increasing actual fill while persistent expansion debt already >0','globalFloor':'min(cumulative UP shares,cumulative DOWN shares)-cumulative actual cost','note':'No fee adjustment; same accounting used in prior Target payoff audits.'},
         'groups':{}}
    for asset in ('BTC','ETH'):
        rr=[r for r in rows if r['asset']==asset]
        ff=[r for r in rr if r['firstReexpand']]
        out['groups'][asset]={'ALL_REEXPAND':summarize(rr),'FIRST_REEXPAND_PER_EPISODE':summarize(ff),
                              'FIRST_BY_PROGRESS':{k:summarize([r for r in ff if lo<=r['repairProgressFrac']<(hi if hi<1 else 1+EPS)]) for k,lo,hi in [('P0_25',0,.25),('P25_50',.25,.5),('P50_75',.5,.75),('P75_100',.75,1)]}}
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
    brief={a:{'all':out['groups'][a]['ALL_REEXPAND'],'first':out['groups'][a]['FIRST_REEXPAND_PER_EPISODE']} for a in ('BTC','ETH')}
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'rows':len(rows),'brief':brief},indent=2))
if __name__=='__main__':main()
