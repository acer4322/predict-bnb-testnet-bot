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
DB=ROOT/'data/target_wallet_official_v1.db';PUB=ROOT/'data/public_research_archive_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_v1.json';MODEL=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_v1.joblib';CSV=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_v1_rows.csv'

def load_public():
 con=sqlite3.connect(f'file:{PUB.as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row;rows=defaultdict(list)
 for r in con.execute('select market_id,sampled_at_ms,seconds_left,predict_up_mid,predict_down_mid,spot_minus_strike_bps from wallet_taker_signal_snapshots order by market_id,sampled_at_ms'):rows[int(r['market_id'])].append(dict(r))
 con.close();return rows,{m:[int(x['sampled_at_ms']) for x in xs] for m,xs in rows.items()}
def asof(rows,times,mid,t,maxlag=1500):
 xs=rows.get(mid);ts=times.get(mid)
 if not xs:return None
 j=bisect.bisect_left(ts,int(t))-1
 if j<0:return None
 r=xs[j];lag=int(t)-int(r['sampled_at_ms'])
 return None if lag<=0 or lag>maxlag else r|{'lag_ms':lag}
def pm(st):
 m=core._portfolio_metrics(st);up=float(getattr(st,'up_shares',0));down=float(getattr(st,'down_shares',0));gross=up+down;base=min(up,down);gap=float(m['payoff_gap'])
 return {'floor':float(m['worst_case_pnl']),'risk_deficit':float(m['risk_deficit']),'abs_gap':abs(gap),'payoff_gap':gap,'gross_shares':gross,'base_pair_shares':base,'coverage':base/gross if gross>1e-9 else 0.0}
def build(events,pub,pt):
 by=defaultdict(list)
 for e in events:by[int(e['market_id'])].append(e)
 rows=[];tls={}
 for mid,evs in by.items():
  if mid not in pub:continue
  evs=sorted(evs,key=lambda r:(int(r['event_ms']),str(r['leg_id'])));st=core.PortfolioState();hist=deque();tl=[]
  for i,e in enumerate(evs):
   t=int(e['event_ms']);pre=st.copy();met=pm(pre);p=asof(pub,pt,mid,t)
   while hist and t-hist[0][0]>15000:hist.popleft()
   if p and p.get('seconds_left') is not None and 0<float(p['seconds_left'])<=60 and met['floor']>=0:
    r5=[x for x in hist if t-x[0]<=5000];r15=list(hist);up=float(p['predict_up_mid']) if p.get('predict_up_mid') is not None else np.nan;down=float(p['predict_down_mid']) if p.get('predict_down_mid') is not None else (1-up if np.isfinite(up) else np.nan);spot=float(p['spot_minus_strike_bps']) if p.get('spot_minus_strike_bps') is not None else np.nan;dom='UP' if met['payoff_gap']>0 else 'DOWN' if met['payoff_gap']<0 else 'FLAT';ds=1 if dom=='UP' else -1 if dom=='DOWN' else 0
    rows.append({'market_id':mid,'event_ms':t,'seconds_left':float(p['seconds_left']),**met,'predict_up_mid':up,'predict_edge':abs(up-.5) if np.isfinite(up) else np.nan,'predict_supports_dominant':int(dom!='FLAT' and (('UP' if up>=down else 'DOWN')==dom)) if np.isfinite(up) and np.isfinite(down) else 0,'strike_toward_dominant_bps':spot*ds if np.isfinite(spot) else np.nan,'events_5s':len(r5),'events_15s':len(r15),'maker_events_15s':sum(str(x[1]).upper()=='MAKER' for x in r15),'taker_events_15s':sum(str(x[1]).upper()=='TAKER' for x in r15),'shares_5s':sum(x[3] for x in r5),'shares_15s':sum(x[3] for x in r15),'public_lag_ms':int(p['lag_ms'])})
   core._apply_leg(st,str(e['role']),str(e['side']),str(e['quote_type']),float(e['shares']),float(e['price']));hist.append((t,str(e['role']),str(e['side']),float(e['shares'])));tl.append((t,pm(st)['floor']))
  tls[mid]=tl
 for r in rows:
  t=r['event_ms'];f=[x for x in tls[r['market_id']] if t<x[0]<=t+5000]
  r['floor_relapse_5s']=int(any(x[1]<0 for x in f));r['floor_drawdown_5s']=float(min([x[1] for x in f]+[r['floor']])-r['floor'])
 return pd.DataFrame(rows)
def met(y,p):
 if len(set(y))<2:return {'n':len(y),'positiveRate':float(np.mean(y)) if len(y) else None}
 return {'n':len(y),'positiveRate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 pub,pt=load_public();db=life._connect_ro(DB)
 try:events,audit=life._load_official_events(db,asset='BTC')
 finally:db.close()
 df=build(events,pub,pt).dropna(subset=['floor','abs_gap','coverage','predict_up_mid','strike_toward_dominant_bps']).sort_values(['event_ms','market_id']);CSV.parent.mkdir(parents=True,exist_ok=True);df.to_csv(CSV,index=False)
 mids=df.groupby('market_id').event_ms.min().sort_values().index.tolist();n=len(mids);cut=max(1,int(n*.55));blocks=np.array_split(mids[cut:],3)
 base=['seconds_left','floor','abs_gap','coverage'];info=base+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps'];full=info+['events_5s','events_15s','maker_events_15s','taker_events_15s','shares_5s','shares_15s']
 res={k:[] for k in ['BASE','INFO','FULL']}
 for bi,barr in enumerate(blocks):
  test=set([int(x) for x in barr.tolist()]);first=min([mids.index(x) for x in test],default=n);train=set(mids[:first])
  for name,feats in [('BASE',base),('INFO',info),('FULL',full)]:
   tr=df[df.market_id.isin(train)].dropna(subset=feats);te=df[df.market_id.isin(test)].dropna(subset=feats)
   if tr.empty or te.empty or tr.floor_relapse_5s.nunique()<2 or te.floor_relapse_5s.nunique()<2:res[name].append({'block':bi,'n':len(te),'positiveRate':float(te.floor_relapse_5s.mean()) if len(te) else None});continue
   m=HistGradientBoostingClassifier(max_depth=3,max_iter=180,learning_rate=.05,min_samples_leaf=18,l2_regularization=1,random_state=20260828+bi).fit(tr[feats],tr.floor_relapse_5s);p=m.predict_proba(te[feats])[:,1];z=met(te.floor_relapse_5s,p);z|={'block':bi,'markets':len(test)};res[name].append(z)
 def agg(name):
  xs=[x for x in res[name] if 'auc'in x];return {'eligibleBlocks':len(xs),'meanAuc':float(np.mean([x['auc'] for x in xs])) if xs else None,'worstAuc':float(np.min([x['auc'] for x in xs])) if xs else None,'meanAp':float(np.mean([x['ap'] for x in xs])) if xs else None,'meanLogLoss':float(np.mean([x['logLoss'] for x in xs])) if xs else None,'allAucAboveHalf':bool(xs and all(x['auc']>.5 for x in xs))}
 c=max(1,int(n*.8));trm=set(mids[:c]);tem=set(mids[c:]);models={};hold={}
 for name,feats in [('BASE',base),('INFO',info),('FULL',full)]:
  tr=df[df.market_id.isin(trm)].dropna(subset=feats);te=df[df.market_id.isin(tem)].dropna(subset=feats);m=HistGradientBoostingClassifier(max_depth=3,max_iter=180,learning_rate=.05,min_samples_leaf=18,l2_regularization=1,random_state=20260828).fit(tr[feats],tr.floor_relapse_5s);p=m.predict_proba(te[feats])[:,1];hold[name]=met(te.floor_relapse_5s,p)|{'features':feats};models[name]=m
 rep={'version':'R4_PROTECTION_MANAGER_HOLD_V1','researchOnly':True,'actionAuthority':False,'label':'Final60s, strict-past safe floor>=0: whether realized official event path relapses below floor0 within next5s.','coverage':{'rows':len(df),'markets':int(df.market_id.nunique()),'positiveRate':float(df.floor_relapse_5s.mean()),'medianPublicLagMs':float(df.public_lag_ms.median())},'features':{'BASE':base,'INFO':info,'FULL':full},'forwardBlocks':res,'aggregate':{k:agg(k) for k in res},'frozenHoldout':hold,'guards':['Strict-past realized official legs only for current portfolio.','Future realized legs are labels only.','No winner/settlement or future order features.','No dream fill/full-parent-at-first-event state.','No threshold sweep.']}
 OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');joblib.dump({'version':'R4_PROTECTION_MANAGER_HOLD_V1','models':models,'features':rep['features'],'researchOnly':True},MODEL);print(json.dumps({'artifact':str(OUT.relative_to(ROOT)),'coverage':rep['coverage'],'aggregate':rep['aggregate'],'frozenHoldout':hold},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
