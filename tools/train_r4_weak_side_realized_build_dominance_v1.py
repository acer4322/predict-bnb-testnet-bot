from __future__ import annotations
import sqlite3, json, statistics, datetime, zoneinfo
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'r4_v0'/'hourly'/'r4_weak_side_realized_build_dominance_v1.json'
OUT.parent.mkdir(parents=True,exist_ok=True)

def fee(sh,px): return sh*px*(1-px)*.02*4

def safe_auc(y,p): return float(roc_auc_score(y,p)) if len(set(y))>1 else None

def eval_model(X,y,features,cut):
    m=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=180,l2_regularization=3.0,random_state=42,class_weight='balanced')
    m.fit(X.iloc[:cut][features],y.iloc[:cut]); p=m.predict_proba(X.iloc[cut:][features])[:,1]; yy=y.iloc[cut:]
    return {'trainN':cut,'testN':len(yy),'positiveRate':float(yy.mean()),'auc':safe_auc(yy,p),'ap':float(average_precision_score(yy,p)),'balancedAccuracyAt05':float(balanced_accuracy_score(yy,p>=.5))}

c=sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1",uri=True); c.row_factory=sqlite3.Row
resolved={}
for r in c.execute("select market_id,resolved_at_ms from target_market_results where asset='BTC'"):
    t=int(r['resolved_at_ms'] or 0)
    if not t: continue
    dt=datetime.datetime.fromtimestamp(t/1000,datetime.timezone.utc).astimezone(zoneinfo.ZoneInfo('Asia/Taipei'))
    if dt.date()==datetime.date(2026,8,16): continue
    resolved[int(r['market_id'])]=t
parents={}
for r in c.execute("select market_id,role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where asset='BTC' order by market_id,first_event_ms,parent_id"):
    mid=int(r['market_id'])
    if mid in resolved: parents.setdefault(mid,[]).append(dict(r))

anchors=[]; base_rows={}
for mid,evs in parents.items():
    up=down=cost=fees=0.; tr=[]
    for i,e in enumerate(evs):
        sh=float(e['shares'] or 0); px=float(e['average_price'] or 0); role=e['role']; side=e['side']; t=int(e['first_event_ms'] or 0)
        if side=='UP': up+=sh
        elif side=='DOWN': down+=sh
        cost+=sh*px
        if role=='TAKER': fees+=fee(sh,px)
        pu=up-cost-fees; pdn=down-cost-fees
        tr.append({'i':i,'t':t,'floor':min(pu,pdn),'upside':max(pu,pdn),'up':up,'down':down,'surplus':abs(up-down),'base':min(up,down),'role':role,'side':side,'price':px,'shares':sh})
    fs=next((x for x in tr if x['floor']>=0),None)
    if not fs or fs['i']==0: continue
    post=[x for x in tr if fs['t']<=x['t']<=fs['t']+15000]
    durable=int(bool(post and min(x['floor'] for x in post)>=-5 and post[-1]['floor']>=0))
    pre=tr[fs['i']-1]
    ss='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'; weak='DOWN' if ss=='UP' else 'UP' if ss=='DOWN' else 'FLAT'
    w5=[x for x in tr if pre['t']-5000<=x['t']<=pre['t']]; w15=[x for x in tr if pre['t']-15000<=x['t']<=pre['t']]
    def shs(w,side,role='MAKER'): return sum(x['shares'] for x in w if x['side']==side and x['role']==role) if side!='FLAT' else 0.
    base_rows[mid]={'market_id':mid,'resolved_at_ms':resolved[mid],'y':durable,'pre_floor':pre['floor'],'pre_upside':pre['upside'],'pre_surplus':pre['surplus'],'pre_base':pre['base'],'pre_surplus_ratio':pre['surplus']/(pre['up']+pre['down']+1e-9),'pre_floor_per_base':pre['floor']/(pre['base']+1e-9),'last_price':pre['price'],'last_shares':pre['shares'],'last_role_taker':int(pre['role']=='TAKER'),'weak_parent_shares_5s':shs(w5,weak),'weak_parent_shares_15s':shs(w15,weak),'surplus_parent_shares_15s':shs(w15,ss),'parent_weak_dominance_15s':(shs(w15,weak)+1)/(shs(w15,ss)+1),'events_parent_5s':len(w5),'events_parent_15s':len(w15),'seconds_from_first_event':(pre['t']-tr[0]['t'])/1000.}
    anchors.append((mid,pre['t']-15000,pre['t'],weak,ss))

c.execute('create temp table a(mid integer primary key,t0 integer,t1 integer,weak text,ss text)'); c.executemany('insert into a values(?,?,?,?,?)',anchors)
events=c.execute("select e.market_id,e.side,e.event_ms,e.observed_at_ms,e.price,e.shares,a.t1 from wallet_shadow_target_events e join a on a.mid=e.market_id where e.asset='BTC' and e.role='MAKER' and e.observed_at_ms between a.t0 and a.t1 order by e.market_id,e.observed_at_ms,e.id").fetchall()
by={}
for r in events: by.setdefault(int(r['market_id']),[]).append(r)
def cadence(L,t1,side,ms):
    z=[r for r in L if r['side']==side and int(r['observed_at_ms'])>=t1-ms] if side!='FLAT' else []
    shares=[float(r['shares'] or 0) for r in z]; px=[float(r['price'] or 0) for r in z]; obs=[int(r['observed_at_ms']) for r in z]; gaps=[(b-a)/1000 for a,b in zip(obs,obs[1:])]
    return {'legs':len(z),'shares':sum(shares),'medSize':statistics.median(shares) if shares else 0.,'priceRange':max(px)-min(px) if px else 0.,'medGap':statistics.median(gaps) if gaps else 0.}
rows=[]
for mid,t0,t1,weak,ss in anchors:
    r=dict(base_rows[mid]); L=by.get(mid,[]); w5=cadence(L,t1,weak,5000); w15=cadence(L,t1,weak,15000); s5=cadence(L,t1,ss,5000); s15=cadence(L,t1,ss,15000)
    r.update({'obs_weak_legs_5s':w5['legs'],'obs_weak_shares_5s':w5['shares'],'obs_weak_legs_15s':w15['legs'],'obs_weak_shares_15s':w15['shares'],'obs_weak_med_size_15s':w15['medSize'],'obs_weak_price_range_15s':w15['priceRange'],'obs_weak_med_gap_15s':w15['medGap'],'obs_surplus_legs_5s':s5['legs'],'obs_surplus_shares_5s':s5['shares'],'obs_surplus_legs_15s':s15['legs'],'obs_surplus_shares_15s':s15['shares'],'obs_fill_dominance_5s':(w5['shares']+1)/(s5['shares']+1),'obs_fill_dominance_15s':(w15['shares']+1)/(s15['shares']+1),'obs_leg_dominance_15s':(w15['legs']+1)/(s15['legs']+1)}); rows.append(r)
df=pd.DataFrame(rows).sort_values('resolved_at_ms').reset_index(drop=True); y=df.y.astype(int)
all_features=[x for x in df.columns if x not in ['market_id','resolved_at_ms','y']]
geometry=['pre_floor','pre_upside','pre_surplus','pre_base','pre_surplus_ratio','pre_floor_per_base','last_price','last_shares','last_role_taker','seconds_from_first_event']
parent_flow=['weak_parent_shares_5s','weak_parent_shares_15s','surplus_parent_shares_15s','parent_weak_dominance_15s','events_parent_5s','events_parent_15s']
observed=[x for x in all_features if x.startswith('obs_')]
X=df[all_features].replace([np.inf,-np.inf],np.nan).fillna(0.); cut=len(df)-600
models={'GEOMETRY_ONLY':eval_model(X,y,geometry,cut),'GEOMETRY_PARENT_FLOW':eval_model(X,y,geometry+parent_flow,cut),'GEOMETRY_OBSERVED_FILL':eval_model(X,y,geometry+observed,cut),'FULL_STRICTPAST':eval_model(X,y,geometry+parent_flow+observed,cut)}
n=len(df); a=int(n*.6); b=int(n*.8); feats=geometry+parent_flow+observed
m=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=180,l2_regularization=3.0,random_state=7,class_weight='balanced').fit(X.iloc[:a][feats],y.iloc[:a])
splits={}
for name,(lo,hi) in {'validation':(a,b),'test':(b,n)}.items():
    p=m.predict_proba(X.iloc[lo:hi][feats])[:,1]; yy=y.iloc[lo:hi]; splits[name]={'n':hi-lo,'positiveRate':float(yy.mean()),'auc':safe_auc(yy,p),'ap':float(average_precision_score(yy,p)),'balancedAccuracyAt05':float(balanced_accuracy_score(yy,p>=.5))}
report={'version':'R4_WEAK_SIDE_REALIZED_BUILD_DOMINANCE_V1','definition':'Predict whether first floor>=0 crossing becomes fixed 15s durable locked base, using only strict-past portfolio/parent flow and Target Maker fill legs whose observed_at_ms is already known at the pre-cross checkpoint. Ordinary BTC; Taipei 2026-08-16 excluded; winner/future never feature.','rows':len(df),'positiveRate':float(y.mean()),'featureGroups':{'geometry':geometry,'parentFlow':parent_flow,'observedFill':observed},'olderToRecent600':models,'chronologicalFull':splits}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2))
