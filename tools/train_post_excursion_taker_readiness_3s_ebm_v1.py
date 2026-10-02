from __future__ import annotations
import json,math,sqlite3
from collections import defaultdict
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'; DB=ROOT/'data'/'target_wallet_official_v1.db'
DATA=OUT/'post_excursion_arbitration_states_v1.csv'; ART=OUT/'post_excursion_taker_readiness_3s_ebm_v1.joblib'; REP=OUT/'post_excursion_taker_readiness_3s_ebm_v1_report.json'
BASE_REP=OUT/'post_excursion_arbitration_ebm_v1_report.json'; SEED=20260823

def ro():
 c=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True,timeout=30); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def numeric(d,fs):return d[fs].apply(pd.to_numeric,errors='coerce')
def metrics(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float); order=np.argsort(-p);k=max(1,int(math.ceil(len(y)*.1)));top=order[:k]
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1])),'brier':float(brier_score_loss(y,p)),'top10pctPositiveRate':float(y[top].mean()),'top10pctRecall':float(y[top].sum()/y.sum())}
def top_terms(m,n=18):
 imp=list(m.term_importances()); names=list(m.term_names_); ix=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:n]; return [{'term':str(names[i]),'importance':float(imp[i])} for i in ix]

def main():
 d=pd.read_csv(DATA); markets=set(map(int,d.market_id.astype(int).unique().tolist())); starts=defaultdict(list); c=ro()
 try:
  ids=sorted(markets)
  for st in range(0,len(ids),250):
   b=ids[st:st+250]; qs=','.join('?'*len(b)); sql=f"select market_id,first_event_ms from target_parent_orders where market_id in ({qs}) and asset='BTC' and role='TAKER' and quote_type='BID' and first_event_ms is not null order by market_id,first_event_ms"
   for r in c.execute(sql,b):starts[int(r['market_id'])].append(int(r['first_event_ms']))
 finally:c.close()
 ys=[]
 for _,r in d.iterrows():
  m=int(r.market_id); cp=int(r.checkpoint_ms); anchor=int(round(cp-float(r.excursion_age_ms))); ft=next((t for t in starts.get(m,[]) if t>anchor),None); ys.append(int(ft is not None and cp<ft<=cp+3000))
 d['label_taker_readiness_3s']=ys
 art3=joblib.load(OUT/'frozen_hazard_3s_full.joblib'); fs3=list(art3['features']); d['taker_pressure_3s']=art3['model'].predict_proba(numeric(d,fs3))[:,1]
 base=json.loads(BASE_REP.read_text(encoding='utf-8')); fs=list(base['features'])+['taker_pressure_3s'];
 ms=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']); ids=ms.market_id.astype(int).tolist(); n=len(ids); a=int(n*.65);b=int(n*.80);cc=int(n*.90); splits={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:cc]),'lateRetrospective':set(ids[cc:])}
 parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in splits.items()}; m=ExplainableBoostingClassifier(feature_names=fs,max_bins=96,max_interaction_bins=32,interactions=8,outer_bags=4,learning_rate=.035,max_rounds=1800,early_stopping_rounds=80,min_samples_leaf=12,n_jobs=-2,random_state=SEED); m.fit(numeric(parts['train'],fs),parts['train'].label_taker_readiness_3s.astype(int))
 joblib.dump({'version':'POST_EXCURSION_TAKER_READINESS_3S_EBM_V1','task':'TAKER_ESCALATION_READINESS_3S','features':fs,'model':m,'trainingMarkets':sorted(splits['train'])},ART)
 out={}
 for k,q in parts.items():
  y=q.label_taker_readiness_3s.astype(int).to_numpy(); bp=q.taker_pressure_3s.to_numpy(float); ep=m.predict_proba(numeric(q,fs))[:,1]; out[k]={'baselineFrozen3s':metrics(y,bp),'ebmReadiness3s':metrics(y,ep),'deltaAuc':float(roc_auc_score(y,ep)-roc_auc_score(y,bp)),'deltaAP':float(average_precision_score(y,ep)-average_precision_score(y,bp))}
 rep={'reportVersion':'POST_EXCURSION_TAKER_READINESS_3S_EBM_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'semantic':'At an unresolved post-excursion checkpoint before the first post-excursion Taker, will that first Target Taker parent begin within next 3s? Re-evaluate every second; this is readiness, not the final order trigger.','positiveRate':float(d.label_taker_readiness_3s.mean()),'splitMarkets':{k:len(v) for k,v in splits.items()},'metrics':out,'topTerms':top_terms(m),'artifact':str(ART),'guards':['3s horizon was motivated before training by the observed 3-5s escalation ramp and ~7s median Taker repair delay; not selected by a score sweep.','Same EBM capacity as the 1s arbitration V1; no hyperparameter search.','LateRetrospective has already been exposed by V1 and is not called untouched/fresh.','Future Target action is label only; runtime inputs remain own/public reconstructable.']}; REP.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
