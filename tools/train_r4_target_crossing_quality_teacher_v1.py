from __future__ import annotations
import sqlite3, json, math, statistics
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data'/'research'/'r4_v0'/'r4_target_crossing_quality_teacher_v1.json'
FEE_BPS=200; H=15000; TOL=-5.0; EPS=1e-9
def taker_fee(sh,px,bps=200): return sh*px*(1-px)*(bps/10000.0)*4.0
def med(xs):
    xs=[x for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.median(xs) if xs else None
c=sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1",uri=True); c.row_factory=sqlite3.Row
resolved={int(r['market_id']):int(r['resolved_at_ms'] or 0) for r in c.execute("select market_id,resolved_at_ms from target_market_results where asset='BTC'")}
markets={}
for r in c.execute("select market_id,role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where asset='BTC' order by market_id,first_event_ms,parent_id"):
    mid=int(r['market_id'])
    if mid in resolved: markets.setdefault(mid,[]).append(r)
rows=[]; desc=[]
for mid,evs in markets.items():
    up=down=cost=fees=0.0; tr=[]
    for i,e in enumerate(evs):
        sh=float(e['shares'] or 0); px=float(e['average_price'] or 0); role=str(e['role']); side=str(e['side']); t=int(e['first_event_ms'] or 0)
        if side=='UP': up+=sh
        elif side=='DOWN': down+=sh
        cost+=sh*px
        if role=='TAKER': fees+=taker_fee(sh,px)
        pu=up-cost-fees; pdown=down-cost-fees
        tr.append({'i':i,'t':t,'role':role,'side':side,'price':px,'shares':sh,'up':up,'down':down,'floor':min(pu,pdown),'upside':max(pu,pdown),'surplus':abs(up-down),'base':min(up,down)})
    fs=next((x for x in tr if x['floor']>=0),None)
    if fs is None or fs['i']==0: continue
    wpost=[y for y in tr if fs['t']<=y['t']<=fs['t']+H]
    durable=bool(wpost and min(y['floor'] for y in wpost)>=TOL and wpost[-1]['floor']>=0)
    j=fs['i']-1; pre=tr[j]
    def win(ms): return [x for x in tr if pre['t']-ms<=x['t']<=pre['t']]
    w5,w15,w30,w60=win(5000),win(15000),win(30000),win(60000)
    surplus_side='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'; weak_side='DOWN' if surplus_side=='UP' else 'UP' if surplus_side=='DOWN' else 'FLAT'
    def shs(w,side,role=None): return sum(x['shares'] for x in w if x['side']==side and (role is None or x['role']==role))
    prev5=w5[0] if w5 else pre
    rows.append({'market_id':mid,'resolved_at_ms':resolved[mid],'y':int(durable),'pre_floor':pre['floor'],'pre_upside':pre['upside'],'pre_surplus':pre['surplus'],'pre_base':pre['base'],'pre_surplus_ratio':pre['surplus']/(pre['up']+pre['down']+EPS),'pre_floor_per_base':pre['floor']/(pre['base']+EPS),'pre_upside_per_surplus':pre['upside']/(pre['surplus']+EPS),'last_price':pre['price'],'last_shares':pre['shares'],'last_role_taker':int(pre['role']=='TAKER'),'age_since_prev_ms':pre['t']-tr[j-1]['t'] if j>0 else 0,'events_5s':len(w5),'events_15s':len(w15),'events_30s':len(w30),'events_60s':len(w60),'maker_events_15s':sum(x['role']=='MAKER' for x in w15),'taker_events_15s':sum(x['role']=='TAKER' for x in w15),'weak_maker_shares_5s':shs(w5,weak_side,'MAKER') if weak_side!='FLAT' else 0,'weak_maker_shares_15s':shs(w15,weak_side,'MAKER') if weak_side!='FLAT' else 0,'weak_maker_shares_30s':shs(w30,weak_side,'MAKER') if weak_side!='FLAT' else 0,'surplus_maker_shares_15s':shs(w15,surplus_side,'MAKER') if surplus_side!='FLAT' else 0,'surplus_maker_shares_30s':shs(w30,surplus_side,'MAKER') if surplus_side!='FLAT' else 0,'weak_taker_shares_15s':shs(w15,weak_side,'TAKER') if weak_side!='FLAT' else 0,'floor_change_5s':pre['floor']-prev5['floor'],'surplus_change_5s':pre['surplus']-prev5['surplus'],'weak_to_surplus_maker_ratio_15s':(shs(w15,weak_side,'MAKER')+1)/(shs(w15,surplus_side,'MAKER')+1) if weak_side!='FLAT' else 1,'weak_to_surplus_maker_ratio_30s':(shs(w30,weak_side,'MAKER')+1)/(shs(w30,surplus_side,'MAKER')+1) if weak_side!='FLAT' else 1,'seconds_from_first_event':(pre['t']-tr[0]['t'])/1000.0})
    desc.append({'durable':durable,'preFloor':pre['floor'],'preSurplus':pre['surplus'],'crossFloor':fs['floor'],'crossSurplus':fs['surplus']})
df=pd.DataFrame(rows).sort_values('resolved_at_ms').reset_index(drop=True); features=[x for x in df.columns if x not in ['market_id','resolved_at_ms','y']]
X=df[features].replace([np.inf,-np.inf],np.nan).fillna(0.0); y=df.y.astype(int); n=len(df); a=int(n*.6); b=int(n*.8)
model=HistGradientBoostingClassifier(max_depth=5,learning_rate=.05,max_iter=220,l2_regularization=2.0,random_state=42).fit(X.iloc[:a],y.iloc[:a])
splits={}
for name,(lo,hi) in {'train':(0,a),'validation':(a,b),'test':(b,n)}.items():
    p=model.predict_proba(X.iloc[lo:hi])[:,1]; yy=y.iloc[lo:hi]; splits[name]={'n':hi-lo,'positiveRate':float(yy.mean()),'auc':float(roc_auc_score(yy,p)),'ap':float(average_precision_score(yy,p)),'balancedAccuracyAt05':float(balanced_accuracy_score(yy,p>=.5))}
cut=n-600; m2=HistGradientBoostingClassifier(max_depth=5,learning_rate=.05,max_iter=220,l2_regularization=2.0,random_state=43).fit(X.iloc[:cut],y.iloc[:cut]); p=m2.predict_proba(X.iloc[cut:])[:,1]; yy=y.iloc[cut:]
ind={'trainN':cut,'testN':600,'testPositiveRate':float(yy.mean()),'auc':float(roc_auc_score(yy,p)),'ap':float(average_precision_score(yy,p)),'balancedAccuracyAt05':float(balanced_accuracy_score(yy,p>=.5))}
D=[r for r in desc if r['durable']]; F=[r for r in desc if not r['durable']]
summary={'marketsWithFirstSafe':n,'firstSafeImmediatelyDurable':len(D),'rate':len(D)/n,'durableMedianPreFloor':med([r['preFloor'] for r in D]),'fragileMedianPreFloor':med([r['preFloor'] for r in F]),'durableMedianPreSurplus':med([r['preSurplus'] for r in D]),'fragileMedianPreSurplus':med([r['preSurplus'] for r in F]),'durableMedianCrossFloor':med([r['crossFloor'] for r in D]),'fragileMedianCrossFloor':med([r['crossFloor'] for r in F]),'durableMedianCrossSurplus':med([r['crossSurplus'] for r in D]),'fragileMedianCrossSurplus':med([r['crossSurplus'] for r in F])}
out={'version':'R4_TARGET_CROSSING_QUALITY_TEACHER_V1','definition':'Strict-past checkpoint immediately before first floor>=0 crossing predicts whether first crossing is durable for fixed 15s rule; winner/future excluded from features; chronological split.','features':features,'summary':summary,'splits':splits,'olderToRecent600':ind}; OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2))