from __future__ import annotations
import bisect,json,math,sqlite3,sys
from collections import defaultdict,Counter
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import analyze_target_maker_taker_inventory_lifecycle_v1 as life
from tools import analyze_target_controller_parameter_extraction_v1 as core

DB=ROOT/'data/target_wallet_official_v1.db'
PUB=ROOT/'data/public_research_archive_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/target_strike_distance_formation_mode_rebuild_v2.json'
CSV=ROOT/'data/research/r4_v0/hourly/target_strike_distance_formation_mode_rebuild_v2_rows.csv'
EPS=1e-9
DIST_BINS=[-1e9,-10,-5,-2,0,2,5,10,1e9]
DIST_LABELS=['<-10','-10:-5','-5:-2','-2:0','0:2','2:5','5:10','10+']
TIME_BINS=[0,30,60,120,180,240,301]
TIME_LABELS=['0-30','30-60','60-120','120-180','180-240','240-300']
CONF_BINS=[0,.05,.1,.2,.3,.5001]
CONF_LABELS=['0-.05','.05-.1','.1-.2','.2-.3','.3-.5']

def load_public():
    con=sqlite3.connect(f'file:{PUB.as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
    rows=defaultdict(list)
    q='''select market_id,sampled_at_ms,seconds_left,predict_up_mid,predict_down_mid,spot_minus_strike_bps,spot_price,strike_price,feature_completeness_ratio from wallet_taker_signal_snapshots order by market_id,sampled_at_ms'''
    for r in con.execute(q): rows[int(r['market_id'])].append(dict(r))
    con.close()
    times={m:[int(x['sampled_at_ms']) for x in xs] for m,xs in rows.items()}
    return rows,times

def asof(rows,times,mid,t,maxlag=1500):
    xs=rows.get(mid); ts=times.get(mid)
    if not xs:return None
    j=bisect.bisect_left(ts,int(t))-1
    if j<0:return None
    r=xs[j]; lag=int(t)-int(r['sampled_at_ms'])
    if lag<=0 or lag>maxlag:return None
    return r|{'lag_ms':lag}

def parent_before_states(events,parents_by_market):
    out=[]
    by_event=defaultdict(list)
    for e in events: by_event[int(e['market_id'])].append(e)
    for mid in sorted(set(by_event)&set(parents_by_market)):
        evs=sorted(by_event[mid],key=lambda r:(int(r['event_ms']),str(r['leg_id'])))
        pmap={str(p['parent_id']):p for p in parents_by_market[mid]}
        first={}; last={}
        for e in evs:
            pid=str(e['parent_id']);k=(int(e['event_ms']),str(e['leg_id']))
            first[pid]=min(first.get(pid,k),k);last[pid]=max(last.get(pid,k),k)
        st=core.PortfolioState(); before={}
        for e in evs:
            pid=str(e['parent_id']); k=(int(e['event_ms']),str(e['leg_id']))
            if k==first[pid]: before[pid]=st.copy()
            core._apply_leg(st,str(e['role']),str(e['side']),str(e['quote_type']),float(e['shares']),float(e['price']))
            if k!=last[pid]:continue
            p=pmap.get(pid)
            if p is None or str(p['role']).upper()!='MAKER' or str(p['quote_type']).upper()!='BID':continue
            b=before[pid]; bm=core._portfolio_metrics(b); am=core._portfolio_metrics(core._apply_parent_cf(b,p)); eff=core._portfolio_effect(bm,am)
            if eff=='EXPOSURE_ADD': mode='ADD'
            elif eff in {'RISK_REDUCING','GAP_REDUCING_EXPENSIVE','TAIL_IMPROVING'}: mode='REPAIR'
            else: continue
            gap=float(bm['payoff_gap'])
            if abs(gap)<=EPS: continue
            dom='UP' if gap>0 else 'DOWN'; weak='DOWN' if dom=='UP' else 'UP'
            out.append({'market_id':mid,'parent_id':pid,'first_event_ms':int(p['first_event_ms']),'side':str(p['side']).upper(),'shares':float(p['shares']),'price':float(p['average_price']),'mode':mode,'is_add':int(mode=='ADD'),'dominant_side':dom,'weak_side':weak,'pre_payoff_gap':gap,'pre_abs_payoff_gap':abs(gap),'pre_risk_deficit':float(bm['risk_deficit']),'pre_worst_case_pnl':float(bm['worst_case_pnl']),'pre_maker_abs_gap':float(bm['maker_abs_payoff_gap'])})
    return out

def metric(y,p):
    if len(set(y))<2:return {'n':len(y)}
    return {'n':len(y),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1])),'positiveRate':float(np.mean(y))}

def model_eval(df,features):
    mids=df.groupby('market_id').first().sort_values('first_event_ms').index.tolist(); n=len(mids);a=max(1,int(n*.70));b=max(a+1,int(n*.85));b=min(b,n)
    tr=set(mids[:a]);va=set(mids[a:b]);te=set(mids[b:])
    res={}
    for name,ss in [('validation',va),('test',te)]:
        train=df[df.market_id.isin(tr)].dropna(subset=features); ev=df[df.market_id.isin(ss)].dropna(subset=features)
        if train.empty or ev.empty: res[name]={'n':0};continue
        m=HistGradientBoostingClassifier(max_depth=3,max_iter=220,learning_rate=.05,min_samples_leaf=20,l2_regularization=1.,random_state=20260827).fit(train[features],train.is_add)
        p=m.predict_proba(ev[features])[:,1];res[name]=metric(ev.is_add.values,p)
    res['features']=features;res['splitMarkets']={'train':len(tr),'validation':len(va),'test':len(te)}
    return res

def grouped(df,cols):
    z=df.groupby(cols,observed=True).agg(n=('is_add','size'),addRate=('is_add','mean'),meanAbsGap=('pre_abs_payoff_gap','mean'),meanRisk=('pre_risk_deficit','mean'),meanSec=('seconds_left','mean')).reset_index()
    return json.loads(z.to_json(orient='records'))

def main():
    pub,pt=load_public(); pub_mids=set(pub)
    db=life._connect_ro(DB)
    try: events,audit=life._load_official_events(db,asset='BTC'); parents=life._build_parents(events)
    finally: db.close()
    pb=defaultdict(list)
    for p in parents:
        mid=int(p['market_id'])
        if mid in pub_mids: pb[mid].append(p)
    raw=parent_before_states([e for e in events if int(e['market_id']) in pub_mids],pb)
    rows=[]
    for r in raw:
        s=asof(pub,pt,int(r['market_id']),int(r['first_event_ms']),1500)
        if not s or s.get('spot_minus_strike_bps') is None or s.get('predict_up_mid') is None:continue
        spot=float(s['spot_minus_strike_bps']); up=float(s['predict_up_mid']); down=float(s['predict_down_mid']) if s.get('predict_down_mid') is not None else 1-up
        domsign=1. if r['dominant_side']=='UP' else -1.; sidesign=1. if r['side']=='UP' else -1.
        row=r|{'sampled_at_ms':int(s['sampled_at_ms']),'public_lag_ms':int(s['lag_ms']),'seconds_left':float(s['seconds_left']) if s.get('seconds_left') is not None else np.nan,'predict_up_mid':up,'predict_down_mid':down,'predict_edge':abs(up-.5),'predict_favored_side':'UP' if up>=down else 'DOWN','spot_minus_strike_bps':spot,'strike_toward_dominant_bps':spot*domsign,'strike_toward_parent_bps':spot*sidesign,'spot_supports_dominant':int(spot*domsign>0),'predict_supports_dominant':int(('UP' if up>=down else 'DOWN')==r['dominant_side']),'parent_is_dominant':int(r['side']==r['dominant_side'])}
        rows.append(row)
    df=pd.DataFrame(rows).sort_values(['first_event_ms','market_id'])
    df['distance_bin']=pd.cut(df.strike_toward_dominant_bps,DIST_BINS,labels=DIST_LABELS,right=False)
    df['time_bin']=pd.cut(df.seconds_left,TIME_BINS,labels=TIME_LABELS,right=False)
    df['conf_bin']=pd.cut(df.predict_edge,CONF_BINS,labels=CONF_LABELS,right=False)
    CSV.parent.mkdir(parents=True,exist_ok=True);df.to_csv(CSV,index=False)
    base=['seconds_left','predict_up_mid','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
    strike=base+['strike_toward_dominant_bps']
    interaction=strike+['predict_supports_dominant']
    rep={'version':'TARGET_STRIKE_DISTANCE_FORMATION_MODE_REBUILD_V2','researchOnly':True,'strictPastPublic':True,'definition':{'label':'Maker BID parent classified ADD if observed full-parent counterfactual increases strict-past |combined payoff gap|; REPAIR if it decreases gap or improves floor/tail. Pre-parent state is event-level strict past.','dominant':'UP when pre-parent settle_up-settle_down >0, DOWN when <0. Flat pre-gap excluded.','strikeSigned':'spot_minus_strike_bps projected toward current pre-parent dominant side; positive means spot/strike supports existing asymmetry.','publicJoin':'latest sampled_at_ms < parent first_event_ms; max lag 1500ms.'},'coverage':{'rows':len(df),'markets':int(df.market_id.nunique()),'add':int(df.is_add.sum()),'repair':int((1-df.is_add).sum()),'addRate':float(df.is_add.mean()),'medianPublicLagMs':float(df.public_lag_ms.median()),'p90PublicLagMs':float(df.public_lag_ms.quantile(.9))},'byDistance':grouped(df,['distance_bin']),'byTimeDistance':grouped(df,['time_bin','distance_bin']),'byConfidenceDistance':grouped(df,['conf_bin','distance_bin']),'byPredictAgreementDistance':grouped(df,['predict_supports_dominant','distance_bin']),'model':{'native':model_eval(df,base),'nativePlusStrike':model_eval(df,strike),'nativePlusStrikeAgreement':model_eval(df,interaction)},'guards':['No winner/settlement result used as feature.','Public snapshot equal timestamps excluded.','Observed Target fills used retrospectively only to label Maker Formation mode.','No threshold sweep; fixed bins reuse prior strike-distance research bins.','BID Maker parents only; ASK lifecycle excluded from this Formation test.']}
    for part in ('validation','test'):
        a=rep['model']['native'].get(part,{});b=rep['model']['nativePlusStrike'].get(part,{});c=rep['model']['nativePlusStrikeAgreement'].get(part,{})
        if 'auc'in a and 'auc'in b: b['deltaAucVsNative']=b['auc']-a['auc'];b['logLossImprovementVsNative']=a['logLoss']-b['logLoss']
        if 'auc'in a and 'auc'in c: c['deltaAucVsNative']=c['auc']-a['auc'];c['logLossImprovementVsNative']=a['logLoss']-c['logLoss']
    OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)),'rows':rep['coverage'],'byDistance':rep['byDistance'],'model':rep['model']},ensure_ascii=False))
if __name__=='__main__':main()
