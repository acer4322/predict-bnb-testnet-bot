from __future__ import annotations
import bisect,json,sqlite3,sys
from collections import defaultdict,deque
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
import joblib

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_target_maker_taker_inventory_lifecycle_v1 as life
from tools import analyze_target_controller_parameter_extraction_v1 as core

DB=ROOT/'data/target_wallet_official_v1.db'
PUB=ROOT/'data/public_research_archive_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_readiness_v0.json'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_readiness_v0.joblib'
CSV=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_readiness_v0_rows.csv'


def load_public():
    con=sqlite3.connect(f'file:{PUB.as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
    rows=defaultdict(list)
    q='''select market_id,sampled_at_ms,seconds_left,predict_up_mid,predict_down_mid,spot_minus_strike_bps from wallet_taker_signal_snapshots order by market_id,sampled_at_ms'''
    for r in con.execute(q):rows[int(r['market_id'])].append(dict(r))
    con.close(); times={m:[int(x['sampled_at_ms']) for x in xs] for m,xs in rows.items()}
    return rows,times

def asof(rows,times,mid,t,maxlag=1500):
    xs=rows.get(mid);ts=times.get(mid)
    if not xs:return None
    j=bisect.bisect_left(ts,int(t))-1
    if j<0:return None
    r=xs[j];lag=int(t)-int(r['sampled_at_ms'])
    if lag<=0 or lag>maxlag:return None
    return r|{'lag_ms':lag}

def pm(st):
    m=core._portfolio_metrics(st)
    up=float(getattr(st,'up_shares',0.0));down=float(getattr(st,'down_shares',0.0))
    gross=up+down;base=min(up,down);gap=float(m['payoff_gap'])
    return {
      'floor':float(m['worst_case_pnl']), 'risk_deficit':float(m['risk_deficit']),
      'abs_gap':abs(gap), 'payoff_gap':gap, 'gross_shares':gross,'base_pair_shares':base,
      'coverage':base/gross if gross>1e-9 else 0.0,
    }

def build_rows(events,pub,pt):
    by=defaultdict(list)
    for e in events:by[int(e['market_id'])].append(e)
    out=[]
    for mid,evs in by.items():
        if mid not in pub:continue
        evs=sorted(evs,key=lambda r:(int(r['event_ms']),str(r['leg_id'])))
        st=core.PortfolioState();hist=deque(); states=[]
        # pre-event snapshots, then apply observed realized leg
        for i,e in enumerate(evs):
            t=int(e['event_ms']);p=asof(pub,pt,mid,t)
            pre=st.copy(); met=pm(pre)
            while hist and t-hist[0][0]>15000:hist.popleft()
            if p and p.get('seconds_left') is not None and 0<float(p['seconds_left'])<=60 and met['floor']<0:
                r5=[x for x in hist if t-x[0]<=5000];r15=list(hist)
                up=float(p['predict_up_mid']) if p.get('predict_up_mid') is not None else np.nan
                down=float(p['predict_down_mid']) if p.get('predict_down_mid') is not None else (1-up if np.isfinite(up) else np.nan)
                spot=float(p['spot_minus_strike_bps']) if p.get('spot_minus_strike_bps') is not None else np.nan
                dom='UP' if met['payoff_gap']>0 else 'DOWN' if met['payoff_gap']<0 else 'FLAT'
                domsign=1. if dom=='UP' else -1. if dom=='DOWN' else 0.
                out.append({
                  'market_id':mid,'event_ms':t,'event_index':i,'seconds_left':float(p['seconds_left']),
                  **met,
                  'predict_up_mid':up,'predict_edge':abs(up-.5) if np.isfinite(up) else np.nan,
                  'predict_supports_dominant':int(dom!='FLAT' and (('UP' if up>=down else 'DOWN')==dom)) if np.isfinite(up) and np.isfinite(down) else 0,
                  'strike_toward_dominant_bps':spot*domsign if np.isfinite(spot) else np.nan,
                  'events_5s':float(len(r5)),'events_15s':float(len(r15)),
                  'maker_events_15s':float(sum(str(x[1]).upper()=='MAKER' for x in r15)),
                  'taker_events_15s':float(sum(str(x[1]).upper()=='TAKER' for x in r15)),
                  'shares_5s':float(sum(x[3] for x in r5)),'shares_15s':float(sum(x[3] for x in r15)),
                  'public_lag_ms':int(p['lag_ms'])
                })
            role=str(e['role']);side=str(e['side']);qt=str(e['quote_type']);sh=float(e['shares']);px=float(e['price'])
            core._apply_leg(st,role,side,qt,sh,px); hist.append((t,role,side,sh))
            states.append((t,pm(st)['floor']))
        # label from realized future event states only. positive = crosses >=0 within 5s and does not relapse below 0 before t+5s after first crossing.
        if not out:continue
    # Build state timelines once for labels
    timelines={}
    for mid,evs in by.items():
        if mid not in pub:continue
        st=core.PortfolioState();tl=[]
        for e in sorted(evs,key=lambda r:(int(r['event_ms']),str(r['leg_id']))):
            core._apply_leg(st,str(e['role']),str(e['side']),str(e['quote_type']),float(e['shares']),float(e['price']))
            tl.append((int(e['event_ms']),pm(st)['floor']))
        timelines[mid]=tl
    for r in out:
        t=r['event_ms']; tl=timelines[r['market_id']]
        fut=[x for x in tl if t < x[0] <= t+5000]
        cross_idx=next((j for j,x in enumerate(fut) if x[1]>=0),None)
        if cross_idx is None:r['safe_cross_5s']=0;r['durable_safe_cross_5s']=0;continue
        r['safe_cross_5s']=1
        after=fut[cross_idx:]
        r['durable_safe_cross_5s']=int(all(x[1]>=0 for x in after))
    return pd.DataFrame(out)

def metrics(y,p):
    if len(set(y))<2:return {'n':len(y),'positiveRate':float(np.mean(y)) if len(y) else None}
    return {'n':len(y),'positiveRate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
    pub,pt=load_public();db=life._connect_ro(DB)
    try:events,audit=life._load_official_events(db,asset='BTC')
    finally:db.close()
    df=build_rows(events,pub,pt)
    df=df.dropna(subset=['seconds_left','floor','risk_deficit','abs_gap','coverage','predict_up_mid','strike_toward_dominant_bps']).sort_values(['event_ms','market_id'])
    CSV.parent.mkdir(parents=True,exist_ok=True);df.to_csv(CSV,index=False)
    mids=df.groupby('market_id').event_ms.min().sort_values().index.tolist();n=len(mids)
    # expanding chronology: initial 55%, then 3 forward blocks from remaining chronology
    cut=max(1,int(n*.55)); rem=mids[cut:]; blocks=np.array_split(rem,3)
    base=['seconds_left','floor','risk_deficit','abs_gap','coverage']
    info=base+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps']
    full=info+['events_5s','events_15s','maker_events_15s','taker_events_15s','shares_5s','shares_15s']
    res={k:[] for k in ['BASE','INFO','FULL']}; models={}
    for bi,barr in enumerate(blocks):
        test=set([int(x) for x in barr.tolist()]); first=min([mids.index(x) for x in test],default=n); train=set(mids[:first])
        for name,features in [('BASE',base),('INFO',info),('FULL',full)]:
            tr=df[df.market_id.isin(train)].dropna(subset=features);te=df[df.market_id.isin(test)].dropna(subset=features)
            if tr.empty or te.empty or te.durable_safe_cross_5s.nunique()<2 or tr.durable_safe_cross_5s.nunique()<2:
                res[name].append({'block':bi,'n':len(te)});continue
            m=HistGradientBoostingClassifier(max_depth=3,max_iter=180,learning_rate=.05,min_samples_leaf=18,l2_regularization=1.0,random_state=20260827+bi).fit(tr[features],tr.durable_safe_cross_5s)
            p=m.predict_proba(te[features])[:,1];z=metrics(te.durable_safe_cross_5s.values,p);z|={'block':bi,'markets':len(test)};res[name].append(z)
    # final frozen research model on first 80%, holdout last20
    c=max(1,int(n*.8));trm=set(mids[:c]);tem=set(mids[c:]);tr=df[df.market_id.isin(trm)];te=df[df.market_id.isin(tem)]
    frozen={}
    for name,features in [('BASE',base),('INFO',info),('FULL',full)]:
        a=tr.dropna(subset=features);b=te.dropna(subset=features)
        m=HistGradientBoostingClassifier(max_depth=3,max_iter=180,learning_rate=.05,min_samples_leaf=18,l2_regularization=1.0,random_state=20260827).fit(a[features],a.durable_safe_cross_5s)
        p=m.predict_proba(b[features])[:,1];frozen[name]=metrics(b.durable_safe_cross_5s.values,p)|{'features':features};models[name]=m
    def agg(name):
        xs=[x for x in res[name] if 'auc'in x]
        return {'eligibleBlocks':len(xs),'meanAuc':float(np.mean([x['auc'] for x in xs])) if xs else None,'worstAuc':float(np.min([x['auc'] for x in xs])) if xs else None,'meanAp':float(np.mean([x['ap'] for x in xs])) if xs else None,'meanLogLoss':float(np.mean([x['logLoss'] for x in xs])) if xs else None}
    rep={'version':'R4_PROTECTION_MANAGER_READINESS_V0','researchOnly':True,'actionAuthority':False,'label':'Within final60s and strict-past floor<0, future realized official event path crosses floor>=0 within 5s and does not relapse below0 during remaining events in that 5s window.','coverage':{'rows':len(df),'markets':int(df.market_id.nunique()),'positiveRate':float(df.durable_safe_cross_5s.mean()),'safeCrossRate':float(df.safe_cross_5s.mean()),'medianPublicLagMs':float(df.public_lag_ms.median())},'features':{'BASE':base,'INFO':info,'FULL':full},'forwardBlocks':res,'aggregate':{k:agg(k) for k in res},'frozenHoldout':frozen,'guards':['Official realized leg events only for portfolio state.','Current event is not applied before its decision snapshot.','Future realized events are labels only.','No winner/settlement result feature.','Public join requires sampled_at_ms < decision event_ms and lag<=1500ms.','No dream fill or full-parent-at-first-event reconstruction.']}
    OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8')
    joblib.dump({'version':'R4_PROTECTION_MANAGER_READINESS_V0','models':models,'features':rep['features'],'label':rep['label'],'researchOnly':True},MODEL)
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)),'model':str(MODEL.relative_to(ROOT)),'coverage':rep['coverage'],'aggregate':rep['aggregate'],'frozenHoldout':rep['frozenHoldout']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
