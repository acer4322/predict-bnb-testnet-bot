from __future__ import annotations
import json, math, sqlite3
from collections import defaultdict, deque
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_MAKER_PACKAGE_QUANTITY_PAYOFF_V1.json'
EPS=1e-9

def apply_fill(unmatched,side,qty,price):
    opp='DOWN' if side=='UP' else 'UP'; left=qty; reserve=debt=0.0
    while left>EPS and unmatched[opp]:
        oq,op=unmatched[opp][0]; z=min(left,oq); edge=z*(1-(op+price))
        if edge>=0: reserve+=edge
        else: debt+=-edge
        left-=z; oq-=z
        if oq<=EPS: unmatched[opp].popleft()
        else: unmatched[opp][0]=(oq,op)
    if left>EPS: unmatched[side].append((left,price))
    return reserve,debt

def score(y,p):
    return {'n':int(len(y)),'maeLogQty':float(mean_absolute_error(y,p)),'r2LogQty':float(r2_score(y,p)),
            'medianAbsShareError':float(np.median(np.abs(np.expm1(y)-np.expm1(p))))}

def main():
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    ends={int(r['market_id']):int(r['window_end_ms']) for r in con.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    mids=[int(r[0]) for r in con.execute("select distinct market_id from target_parent_orders where asset='ETH' order by market_id") if int(r[0]) in ends]
    rows=[]
    for mid in mids:
        evs=[dict(r) for r in con.execute("select parent_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' and market_id=? and first_event_ms is not null order by first_event_ms,parent_id",(mid,))]
        U=D=CU=CD=0.0; lastU=lastD=None; reserve=debt=0.0; unmatched={'UP':deque(),'DOWN':deque()}; pre=[]
        for e in evs:
            role=str(e['role']); side=str(e['side']).upper(); t=int(e['first_event_ms']); px=float(e['average_price'] or 0); qty=float(e['shares'] or 0)
            C=CU+CD; gross=U+D; pair=min(U,D); gap=abs(U-D); paircov=2*pair/gross if gross>EPS else 1.0; absratio=gap/gross if gross>EPS else 0.0
            floor=pair-C; best=max(U,D)-C; scale=max(C,1.0); au=CU/U if U>EPS else 0; ad=CD/D if D>EPS else 0
            if abs(U-D)<=EPS: weak=None; strong=None
            else: weak='UP' if U<D else 'DOWN'; strong='DOWN' if weak=='UP' else 'UP'
            strong_avg=ad if strong=='DOWN' else (au if strong=='UP' else None); strong_last=lastD if strong=='DOWN' else (lastU if strong=='UP' else None)
            opp='DOWN' if side=='UP' else 'UP'; opp_unmatched=sum(q for q,_ in unmatched[opp])
            pre.append({'U':U,'D':D,'C':C,'gross':gross,'gap':gap,'paircov':paircov,'absratio':absratio,'floor':floor,'best':best,'scale':scale,
                        'au':au,'ad':ad,'weak':weak,'strong':strong,'strong_avg':strong_avg,'strong_last':strong_last,
                        'opp_unmatched_ratio':opp_unmatched/max(gross,1.0),'reserve':reserve,'debt':debt,'netReserve':reserve-debt,
                        'seconds_left':(ends[mid]-t)/1000.0,'t':t,'side':side,'role':role,'px':px})
            if side=='UP': U+=qty; CU+=qty*px; lastU=px
            else: D+=qty; CD+=qty*px; lastD=px
            dr,dd=apply_fill(unmatched,side,qty,px); reserve+=dr; debt+=dd
        i=0
        while i<len(evs):
            e=evs[i]; p=pre[i]; role=str(e['role']); side=str(e['side']).upper(); t0=int(e['first_event_ms']); px=float(e['average_price'] or 0)
            if role=='MAKER' and p['weak']==side and p['gap']>EPS:
                j=i; qty=0.0; npar=0
                while j<len(evs):
                    x=evs[j]
                    if str(x['role'])!='MAKER' or str(x['side']).upper()!=side or int(x['first_event_ms'])-t0>3000: break
                    qty+=float(x['shares'] or 0); npar+=1; j+=1
                avg_edge=(1-(px+p['strong_avg'])) if p['strong_avg'] is not None else 0.0
                last_edge=(1-(px+p['strong_last'])) if p['strong_last'] is not None else 0.0
                rows.append({'mid':mid,'qty':qty,'gap':p['gap'],'nParents':npar,
                    'f':[p['seconds_left'],p['paircov'],p['absratio'],math.log1p(p['gross']),
                         p['floor']/p['scale'],p['best']/p['scale'],p['au'],p['ad'],p['au']-p['ad'],1.0 if side=='UP' else 0.0,px,
                         avg_edge,last_edge,p['opp_unmatched_ratio'],p['reserve']/p['scale'],p['debt']/p['scale'],p['netReserve']/p['scale']]})
                i=max(j,i+1)
            else: i+=1
    con.close()
    mids2=np.array(sorted(set(r['mid'] for r in rows))); a=int(len(mids2)*.6); b=int(len(mids2)*.8); trm=set(mids2[:a]); vam=set(mids2[a:b]); tem=set(mids2[b:])
    X=np.array([r['f'] for r in rows],float); y=np.log1p(np.array([r['qty'] for r in rows],float)); rm=np.array([r['mid'] for r in rows]); gap=np.array([r['gap'] for r in rows],float)
    bal=[0,1,2,3]; econ=list(range(X.shape[1])); tr=np.isin(rm,list(trm)); va=np.isin(rm,list(vam)); te=np.isin(rm,list(tem))
    params=dict(max_iter=200,learning_rate=.05,max_leaf_nodes=31,l2_regularization=1.0,random_state=7)
    mb=HistGradientBoostingRegressor(**params).fit(X[tr][:,bal],y[tr]); me=HistGradientBoostingRegressor(**params).fit(X[tr][:,econ],y[tr])
    out={'version':'TARGET_ETH_MAKER_PACKAGE_QUANTITY_PAYOFF_V1','researchOnly':True,'actionAuthority':False,
         'coverage':{'packages':len(rows),'markets':len(mids2),'trainMarkets':len(trm),'validMarkets':len(vam),'testMarkets':len(tem),
                     'multiParentPackageRate':float(np.mean([r['nParents']>1 for r in rows]))},'splits':{}}
    for nm,m in [('VALID20',va),('TEST20',te)]:
        yy=y[m]; pb=mb.predict(X[m][:,bal]); pe=me.predict(X[m][:,econ]); pg=np.log1p(gap[m]); sb=score(yy,pb); se=score(yy,pe); sg=score(yy,pg)
        out['splits'][nm]={'directGapHeuristic':sg,'balanceModel':sb,'economicModel':se,
          'economicLiftVsBalance':{'maeReductionFraction':(sb['maeLogQty']-se['maeLogQty'])/sb['maeLogQty'],'medianShareErrorReductionFraction':(sb['medianAbsShareError']-se['medianAbsShareError'])/sb['medianAbsShareError']},
          'economicLiftVsGap':{'maeReductionFraction':(sg['maeLogQty']-se['maeLogQty'])/sg['maeLogQty'],'medianShareErrorReductionFraction':(sg['medianAbsShareError']-se['medianAbsShareError'])/sg['medianAbsShareError']}}
    out['interpretation']={'economicLiftBothSplits':all(out['splits'][s]['economicLiftVsBalance']['maeReductionFraction']>0 for s in ('VALID20','TEST20')),
                           'gapCompetitiveTest':out['splits']['TEST20']['directGapHeuristic']['maeLogQty']<=out['splits']['TEST20']['economicModel']['maeLogQty']}
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__':main()
