from __future__ import annotations
import json,sqlite3,sys
from collections import defaultdict
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_target_controller_parameter_extraction_v1 as core
DB=ROOT/'data/target_wallet_official_v1.db'; OUT=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_enter_ordinary_v2.json'; MODEL=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_enter_ordinary_v2.joblib'; CSV=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_enter_ordinary_v2_rows.csv'
CUTOFF=1786896000000; FEATURES=['seconds_left','floor','abs_gap']
def pm(st):
 m=core._portfolio_metrics(st); return float(m['worst_case_pnl']),abs(float(m['payoff_gap']))
def metric(y,p):
 if len(set(y))<2:return {'n':len(y),'positiveRate':float(np.mean(y)) if len(y) else None}
 return {'n':len(y),'positiveRate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def load_rows():
 con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
 wins={int(r['market_id']):int(r['window_end_ms']) for r in con.execute("select market_id,window_end_ms from target_markets where asset='BTC' and window_end_ms is not null")}; evs=defaultdict(list)
 for r in con.execute("select leg_id,market_id,role,side,quote_type,event_ms,price,shares from wallet_shadow_target_events where asset='BTC' and event_ms>=? order by market_id,event_ms,leg_id",(CUTOFF,)):
  mid=int(r['market_id']);
  if mid in wins:evs[mid].append(dict(r))
 con.close();rows=[]
 for mid,xs in evs.items():
  end=wins[mid];st=core.PortfolioState();tl=[];pres=[]
  for e in xs:
   t=int(e['event_ms']);sec=(end-t)/1000.;fl,gap=pm(st)
   if 0<sec<=60 and fl<0:pres.append({'market_id':mid,'event_ms':t,'seconds_left':sec,'floor':fl,'abs_gap':gap})
   core._apply_leg(st,str(e['role']),str(e['side']),str(e['quote_type']),float(e['shares']),float(e['price']));afl,_=pm(st);tl.append((t,afl))
  for r in pres:
   fut=[(tt,v) for tt,v in tl if r['event_ms']<tt<=r['event_ms']+5000];j=next((i for i,x in enumerate(fut) if x[1]>=0),None);r['safe_cross_5s']=int(j is not None);r['durable_safe_cross_5s']=int(j is not None and all(x[1]>=0 for x in fut[j:]));rows.append(r)
 return pd.DataFrame(rows)
def main():
 df=load_rows().sort_values(['event_ms','market_id']);CSV.parent.mkdir(parents=True,exist_ok=True);df.to_csv(CSV,index=False)
 mids=df.groupby('market_id').event_ms.min().sort_values().index.tolist();n=len(mids);cut=max(1,int(n*.55));blocks=np.array_split(mids[cut:],4);folds=[]
 for bi,barr in enumerate(blocks):
  test=set(map(int,barr.tolist()));first=min([mids.index(x) for x in test],default=n);train=set(mids[:first]);tr=df[df.market_id.isin(train)];te=df[df.market_id.isin(test)]
  if tr.empty or te.empty or tr.durable_safe_cross_5s.nunique()<2 or te.durable_safe_cross_5s.nunique()<2:folds.append({'block':bi,'n':len(te),'positiveRate':float(te.durable_safe_cross_5s.mean()) if len(te) else None});continue
  m=HistGradientBoostingClassifier(max_depth=3,max_iter=180,learning_rate=.05,min_samples_leaf=20,l2_regularization=1,random_state=20260830+bi).fit(tr[FEATURES],tr.durable_safe_cross_5s);p=m.predict_proba(te[FEATURES])[:,1];z=metric(te.durable_safe_cross_5s,p);z|={'block':bi,'markets':len(test)};folds.append(z)
 elig=[x for x in folds if 'auc'in x];agg={'eligibleBlocks':len(elig),'meanAuc':float(np.mean([x['auc'] for x in elig])) if elig else None,'worstAuc':float(np.min([x['auc'] for x in elig])) if elig else None,'meanAp':float(np.mean([x['ap'] for x in elig])) if elig else None,'meanLogLoss':float(np.mean([x['logLoss'] for x in elig])) if elig else None,'allBlockAucAboveHalf':bool(elig and all(x['auc']>.5 for x in elig))}
 c=max(1,int(n*.8));trm=set(mids[:c]);tem=set(mids[c:]);tr=df[df.market_id.isin(trm)];te=df[df.market_id.isin(tem)];model=HistGradientBoostingClassifier(max_depth=3,max_iter=180,learning_rate=.05,min_samples_leaf=20,l2_regularization=1,random_state=20260830).fit(tr[FEATURES],tr.durable_safe_cross_5s);ph=model.predict_proba(te[FEATURES])[:,1];hold=metric(te.durable_safe_cross_5s,ph)|{'markets':len(tem)}
 keep=bool(len(elig)==len(blocks) and agg['allBlockAucAboveHalf'] and agg['meanAuc']>=.60 and agg['worstAuc']>=.55 and hold.get('auc',0)>.5)
 rep={'version':'R4_PROTECTION_MANAGER_ENTER_ORDINARY_V2','status':'TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED','researchOnly':True,'actionAuthority':False,'features':FEATURES,'ordinaryCutoffEventMs':CUTOFF,'sealed20260816Excluded':True,'coverage':{'rows':len(df),'markets':int(df.market_id.nunique()),'positiveRate':float(df.durable_safe_cross_5s.mean()),'safeCrossRate':float(df.safe_cross_5s.mean())},'forwardBlocks':folds,'aggregate':agg,'frozenHoldout':hold,'guards':['Strict-past realized official legs only.','Current event excluded.','Future official legs used label-only.','seconds_left from static market end metadata.','No Predict/strike/winner/settlement feature.','No dream fill.','2026-08-16 SEALED excluded.']}
 OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');joblib.dump({'version':rep['version'],'model':model,'features':FEATURES,'researchOnly':True,'actionAuthority':False},MODEL);print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'model':str(MODEL.relative_to(ROOT)).replace('\\','/'),'status':rep['status'],'coverage':rep['coverage'],'aggregate':agg,'holdout':hold,'folds':folds},indent=2))
if __name__=='__main__':main()
