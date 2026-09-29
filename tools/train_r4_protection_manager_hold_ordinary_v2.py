from __future__ import annotations
import json,sqlite3,sys
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
import joblib

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_target_controller_parameter_extraction_v1 as core
DB=ROOT/'data/target_wallet_official_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_ordinary_v2.json'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_ordinary_v2.joblib'
CSV=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_ordinary_v2_rows.csv'
CUTOFF=1786896000000
FEATURES=['seconds_left','floor','abs_gap']

def pm(st):
    m=core._portfolio_metrics(st)
    return float(m['worst_case_pnl']), abs(float(m['payoff_gap']))

def metric(y,p):
    if len(set(y))<2:return {'n':len(y),'positiveRate':float(np.mean(y)) if len(y) else None}
    return {'n':len(y),'positiveRate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def load_rows():
    con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
    wins={int(r['market_id']):int(r['window_end_ms']) for r in con.execute("select market_id,window_end_ms from target_markets where asset='BTC' and window_end_ms is not null")}
    evs=defaultdict(list)
    q="""select leg_id,market_id,role,side,quote_type,event_ms,price,shares from wallet_shadow_target_events where asset='BTC' and event_ms>=? order by market_id,event_ms,leg_id"""
    for r in con.execute(q,(CUTOFF,)):
        mid=int(r['market_id'])
        if mid in wins: evs[mid].append(dict(r))
    con.close()
    rows=[]
    for mid,xs in evs.items():
        end=wins[mid];st=core.PortfolioState();timeline=[];pre_rows=[]
        for i,e in enumerate(xs):
            t=int(e['event_ms']);sec=(end-t)/1000.0;fl,gap=pm(st)
            if 0<sec<=60 and fl>=0:
                pre_rows.append({'market_id':mid,'event_ms':t,'seconds_left':sec,'floor':fl,'abs_gap':gap})
            core._apply_leg(st,str(e['role']),str(e['side']),str(e['quote_type']),float(e['shares']),float(e['price']))
            afl,_=pm(st);timeline.append((t,afl))
        for r in pre_rows:
            fut=[v for tt,v in timeline if r['event_ms']<tt<=r['event_ms']+5000]
            r['floor_relapse_5s']=int(any(v<0 for v in fut))
            rows.append(r)
    return pd.DataFrame(rows)

def main():
    df=load_rows().sort_values(['event_ms','market_id'])
    CSV.parent.mkdir(parents=True,exist_ok=True);df.to_csv(CSV,index=False)
    mids=df.groupby('market_id').event_ms.min().sort_values().index.tolist();n=len(mids)
    cut=max(1,int(n*.55));blocks=np.array_split(mids[cut:],4);folds=[]
    for bi,barr in enumerate(blocks):
        test=set(map(int,barr.tolist())); first=min([mids.index(x) for x in test],default=n);train=set(mids[:first])
        tr=df[df.market_id.isin(train)];te=df[df.market_id.isin(test)]
        if tr.empty or te.empty or tr.floor_relapse_5s.nunique()<2 or te.floor_relapse_5s.nunique()<2:
            folds.append({'block':bi,'n':len(te),'positiveRate':float(te.floor_relapse_5s.mean()) if len(te) else None});continue
        m=HistGradientBoostingClassifier(max_depth=3,max_iter=180,learning_rate=.05,min_samples_leaf=20,l2_regularization=1.0,random_state=20260829+bi).fit(tr[FEATURES],tr.floor_relapse_5s)
        p=m.predict_proba(te[FEATURES])[:,1];z=metric(te.floor_relapse_5s.values,p);z|={'block':bi,'markets':len(test)};folds.append(z)
    eligible=[x for x in folds if 'auc' in x]
    # Frozen on first 80%, evaluate final20
    c=max(1,int(n*.8));trm=set(mids[:c]);tem=set(mids[c:]);tr=df[df.market_id.isin(trm)];te=df[df.market_id.isin(tem)]
    model=HistGradientBoostingClassifier(max_depth=3,max_iter=180,learning_rate=.05,min_samples_leaf=20,l2_regularization=1.0,random_state=20260829).fit(tr[FEATURES],tr.floor_relapse_5s)
    ph=model.predict_proba(te[FEATURES])[:,1];hold=metric(te.floor_relapse_5s.values,ph)|{'markets':len(tem)}
    agg={'eligibleBlocks':len(eligible),'meanAuc':float(np.mean([x['auc'] for x in eligible])) if eligible else None,'worstAuc':float(np.min([x['auc'] for x in eligible])) if eligible else None,'meanAp':float(np.mean([x['ap'] for x in eligible])) if eligible else None,'meanLogLoss':float(np.mean([x['logLoss'] for x in eligible])) if eligible else None,'allBlockAucAboveHalf':bool(eligible and all(x['auc']>.5 for x in eligible))}
    keep=bool(eligible and len(eligible)==len(blocks) and agg['allBlockAucAboveHalf'] and agg['meanAuc']>=.60 and agg['worstAuc']>=.55 and hold.get('auc',0)>.5)
    rep={'version':'R4_PROTECTION_MANAGER_HOLD_ORDINARY_V2','status':'TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED','researchOnly':True,'actionAuthority':False,'ordinaryCutoffEventMs':CUTOFF,'sealed20260816Excluded':True,'features':FEATURES,'coverage':{'rows':len(df),'markets':int(df.market_id.nunique()),'positiveRate':float(df.floor_relapse_5s.mean()),'minEventMs':int(df.event_ms.min()) if len(df) else None,'maxEventMs':int(df.event_ms.max()) if len(df) else None},'forwardBlocks':folds,'aggregate':agg,'frozenHoldout':hold,'fixedKeepRule':{'allBlocksAucAboveHalf':True,'meanAucMin':.60,'worstAucMin':.55,'holdoutAucAboveHalf':True},'guards':['Strict-past realized official legs only for current portfolio.','Current event excluded from state.','Future official realized legs used only as 5s relapse label.','seconds_left from static target_markets.window_end_ms.','No Predict/strike/winner/settlement feature.','No dream fill/full-parent-at-first-event reconstruction.','2026-08-16 SEALED chronology excluded.']}
    OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');joblib.dump({'version':rep['version'],'model':model,'features':FEATURES,'researchOnly':True,'actionAuthority':False},MODEL)
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)),'model':str(MODEL.relative_to(ROOT)),'status':rep['status'],'coverage':rep['coverage'],'aggregate':agg,'holdout':hold,'folds':folds},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
