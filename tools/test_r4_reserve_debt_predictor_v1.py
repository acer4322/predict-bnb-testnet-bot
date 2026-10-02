from __future__ import annotations
import json,lzma,importlib.util,sys,math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import deque
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,average_precision_score
from scipy.stats import spearmanr

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r4_v0/hourly'
P=ROOT/'tools'/'test_r4_upside_retention_after_durable_base_v1.py'
spec=importlib.util.spec_from_file_location('u_rdp',P);u=importlib.util.module_from_spec(spec);assert spec and spec.loader
sys.modules[spec.name]=u;spec.loader.exec_module(u)
FROZEN=u.FROZEN;STRESSES=u.STRESSES;TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_RESERVE_DEBT_PREDICTOR_V1'
GEOM=['floor_per_gross','edge','coverage','absnet_ratio','cost_per_gross','event_index_norm']
DEBT=['debt_per_gross','debt_to_floor','debt_age_sec','spend_5s_per_gross','spend_15s_per_gross','spend_velocity_5v15','sec_since_repay','repay_15s_per_gross','repay_events_15s','durable_age_sec']
FULL=GEOM+DEBT

def div(a,b): return float(a/b) if abs(b)>1e-12 else 0.0

def load_records():
    records=[]
    src=ROOT/'data/hft_forward_paper_v1/markets'
    for p in sorted(src.glob('*.json.xz'))[-600:]:
        try:
            with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
        except Exception:continue
        mid=int(d.get('marketId') or 0)
        if mid in FROZEN:continue
        ev=u.events(d)
        if ev:records.append((mid,ev))
    records.sort(key=lambda x:(x[1][-1]['t'] if x[1] else 0,x[0]))
    return records

def build_rows(ev0,stress='NONE'):
    ev=u.stress_stream(ev0,stress)
    if not ev:return []
    s=(0.,0.,0.,0.);positive_since=None;durable_since=None;debt=0.0;debt_start=None;last_repay=None
    spends=deque();repays=deque();states=[]
    # First pass builds strict-past state for each event; future label added after full path known.
    for i,z0 in enumerate(ev):
        z=dict(z0);t=int(z['t']);pre=u.geom(s);q=float(z['sh'])
        postfull=u.geom(u.apply(s,z,q));reserve=max(0.,pre['floor']-postfull['floor'])
        flag=pre['floor']>0 and reserve>1e-12 and postfull['floor']<=0 and postfull['edge']<0
        sur='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
        if flag:
            rel='SURPLUS_SIDE' if z['side']==sur else ('WEAK_SIDE_CROSS' if sur!='FLAT' else 'FLAT')
            q=u.safe_qty_zero(s,z,q) if rel=='SURPLUS_SIDE' else 0.0
        # durable is observable only after 15s continuously positive before this event
        if durable_since is None and positive_since is not None and pre['floor']>0 and t-positive_since>=15000:
            durable_since=positive_since
        while spends and t-spends[0][0]>15000:spends.popleft()
        while repays and t-repays[0][0]>15000:repays.popleft()
        spend5=sum(v for tt,v in spends if t-tt<=5000);spend15=sum(v for tt,v in spends)
        repay15=sum(v for tt,v in repays);repayn=sum(1 for tt,v in repays if v>1e-12)
        if durable_since is not None and pre['floor']>0:
            gross=max(pre['up']+pre['down'],1e-9)
            x={
              'floor_per_gross':div(pre['floor'],gross),'edge':pre['edge'],'coverage':pre['coverage'],'absnet_ratio':div(pre['absnet'],gross),
              'cost_per_gross':div(pre['cost'],gross),'event_index_norm':i/max(1,len(ev)-1),
              'debt_per_gross':div(debt,gross),'debt_to_floor':div(debt,max(pre['floor'],1e-9)),
              'debt_age_sec':0.0 if debt<=1e-12 or debt_start is None else max(0.,(t-debt_start)/1000.0),
              'spend_5s_per_gross':div(spend5,gross),'spend_15s_per_gross':div(spend15,gross),
              'spend_velocity_5v15':div(spend5/5.0,max(spend15/15.0,1e-9)),
              'sec_since_repay':300.0 if last_repay is None else min(300.0,max(0.,(t-last_repay)/1000.0)),
              'repay_15s_per_gross':div(repay15,gross),'repay_events_15s':float(repayn),
              'durable_age_sec':max(0.,(t-(durable_since+15000))/1000.0)
            }
            states.append({'i':i,'t':t,'pre_floor':pre['floor'],'x':x})
        post=u.geom(u.apply(s,z,q));sp=max(0.,pre['floor']-post['floor'])
        favorable=bool(durable_since is not None and sur!='FLAT' and z['side']==sur and q>1e-12 and pre['floor']>0 and sp>1e-12)
        if favorable:
            if debt<=1e-12:debt_start=t
            debt+=sp;spends.append((t,sp))
        old_sur=sur;s=u.apply(s,z,q);g=u.geom(s)
        if durable_since is not None and old_sur!='FLAT' and z['side']!=old_sur and g['floor']>pre['floor']+1e-12:
            repay=min(debt,g['floor']-pre['floor']);debt=max(0.,debt-repay)
            if repay>1e-12:
                repays.append((t,repay));last_repay=t
                if debt<=1e-12:debt_start=None
        if g['floor']>0:
            if positive_since is None:positive_since=t
        else:
            positive_since=None;durable_since=None;debt=0.0;debt_start=None;spends.clear();repays.clear()
    # Build actual post-event path for labels using same frozen tranche mechanics.
    s=(0.,0.,0.,0.);path=[]
    for i,z0 in enumerate(ev):
        z=dict(z0);pre=u.geom(s);q=float(z['sh']);postfull=u.geom(u.apply(s,z,q));reserve=max(0.,pre['floor']-postfull['floor'])
        flag=pre['floor']>0 and reserve>1e-12 and postfull['floor']<=0 and postfull['edge']<0
        sur='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
        if flag:
            rel='SURPLUS_SIDE' if z['side']==sur else ('WEAK_SIDE_CROSS' if sur!='FLAT' else 'FLAT')
            q=u.safe_qty_zero(s,z,q) if rel=='SURPLUS_SIDE' else 0.0
        s=u.apply(s,z,q);path.append((int(z['t']),u.geom(s)))
    out=[]
    for a in states:
        fut=[g for j,(tt,g) in enumerate(path) if j>=a['i'] and 0<=tt-a['t']<=15000]
        if len(fut)<2:continue
        minfloor=min(g['floor'] for g in fut);relapse=int(minfloor<=0);draw=max(0.,a['pre_floor']-minfloor)
        out.append({'t':a['t'],'x':a['x'],'relapse':relapse,'drawdown':draw})
    return out

def auc_metrics(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float)
    return {'rows':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(set(y))>1 else None}

def rho(y,p):
    if len(y)<3 or np.std(y)<1e-12 or np.std(p)<1e-12:return None
    return float(spearmanr(y,p).statistic)

def fit_eval(train_rows,test_rows,features):
    X=np.asarray([[r['x'][f] for f in features] for r in train_rows],float);y=np.asarray([r['relapse'] for r in train_rows],int)
    Xt=np.asarray([[r['x'][f] for f in features] for r in test_rows],float);yt=np.asarray([r['relapse'] for r in test_rows],int)
    clf=HistGradientBoostingClassifier(max_iter=140,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=3.,random_state=20260826).fit(X,y)
    p=clf.predict_proba(Xt)[:,1]
    reg=HistGradientBoostingRegressor(max_iter=140,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=3.,random_state=20260826).fit(X,np.asarray([r['drawdown'] for r in train_rows],float))
    pd=reg.predict(Xt)
    return {'relapse':auc_metrics(yt,p),'drawdownRows':len(test_rows),'drawdownSpearman':rho(np.asarray([r['drawdown'] for r in test_rows],float),pd)}

def main():
    records=load_records();n=len(records);cut=max(1,int(n*.70));train_rec=records[:cut];test_rec=records[cut:]
    train=[];test_by={s:[] for s in STRESSES}
    for mid,ev in train_rec:train.extend(build_rows(ev,'NONE'))
    for sname in STRESSES:
        for mid,ev in test_rec:test_by[sname].extend(build_rows(ev,sname))
    base=fit_eval(train,test_by['NONE'],GEOM);full=fit_eval(train,test_by['NONE'],FULL)
    stress={}
    # train the same full normal model once, then evaluate transfer to stress test rows
    X=np.asarray([[r['x'][f] for f in FULL] for r in train],float);y=np.asarray([r['relapse'] for r in train],int);yd=np.asarray([r['drawdown'] for r in train],float)
    clf=HistGradientBoostingClassifier(max_iter=140,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=3.,random_state=20260826).fit(X,y)
    reg=HistGradientBoostingRegressor(max_iter=140,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=3.,random_state=20260826).fit(X,yd)
    for sname in STRESSES[1:]:
        rr=test_by[sname]
        if not rr:stress[sname]={'rows':0};continue
        Xt=np.asarray([[r['x'][f] for f in FULL] for r in rr],float);yt=np.asarray([r['relapse'] for r in rr],int);p=clf.predict_proba(Xt)[:,1];pd=reg.predict(Xt)
        stress[sname]={'relapse':auc_metrics(yt,p),'drawdownSpearman':rho(np.asarray([r['drawdown'] for r in rr],float),pd)}
    auc0=base['relapse']['auc'];auc1=full['relapse']['auc'];lift=None if auc0 is None or auc1 is None else auc1-auc0
    eligible=[s for s,v in stress.items() if v.get('relapse',{}).get('rows',0)>=40 and v.get('relapse',{}).get('auc') is not None]
    stress_pass=sum(1 for s in eligible if stress[s]['relapse']['auc']>=.65)
    keep=bool(full['relapse']['rows']>=80 and auc1 is not None and auc1>=.70 and lift is not None and lift>=.03 and full['drawdownSpearman'] is not None and full['drawdownSpearman']>=.35 and len(eligible)>=2 and stress_pass>=2)
    status='KEEP_SIGNAL' if keep else ('INCONCLUSIVE' if full['relapse']['rows']<80 or auc1 is None else 'REJECTED')
    now=datetime.now(TZ);rep={'version':VERSION,'createdAt':now.isoformat(),'candidate':'Predictor-only reserve-debt state after strictly observable durable15 base. Runtime features are strict-past rolling reserve debt amount/age, reserve-spend velocity, and confirmed weak-side repayment cadence plus current economic geometry. Offline labels are next-15s floor relapse and drawdown under frozen relation-aware tranche; no control action.','cohort':{'supportHistories':n,'trainHistories':len(train_rec),'testHistories':len(test_rec),'trainRows':len(train),'testRowsNormal':len(test_by['NONE']),'excludedFrozenEchtgeldMarkets':sorted(FROZEN)},'features':{'geometry':GEOM,'debt':DEBT},'normal':{'geometryOnly':base,'geometryPlusDebt':full,'aucLift':lift},'stressTransfer':stress,'eligibleStressVariants':eligible,'stressPassCount':stress_pass,'gate':'KEEP only if chronological normal test has >=80 rows, relapse AUC>=0.70, AUC lift over geometry-only >=0.03, drawdown Spearman>=0.35, and >=2 eligible execution stresses have relapse AUC>=0.65. Fixed one-shot model; no threshold/hyperparameter sweep.','status':status,'guards':{'special20260816Sealed':True,'noEchtgeldTraining':True,'echtgeldStressDimensionsOnly':True,'strictPastRuntimeFeatures':True,'future15sLabelsOfflineOnly':True,'noWinnerRuntime':True,'noDreamFill':True,'noThresholdSweep':True,'researchOnly':True,'noLiveR3Change':True,'no8781Change':True}}
    path=OUT/f"r4_reserve_debt_predictor_v1_{now.strftime('%Y%m%d_%H%M%S')}.json";path.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'status':status,'cohort':rep['cohort'],'normal':rep['normal'],'eligibleStressVariants':eligible,'stressPassCount':stress_pass,'stressTransfer':stress},ensure_ascii=False))
if __name__=='__main__':main()
