from __future__ import annotations
import bisect,json,math,sqlite3
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
CSV=OUT/'target_general_maker_side_hazard_v1.csv'
DB=ROOT/'data'/'wallet_maker_book_inference.db'
REPORT=OUT/'target_maker_burst_v1_report.json'
UP_ART=OUT/'target_maker_up_burst_multi_v1.joblib'; DN_ART=OUT/'target_maker_down_burst_multi_v1.joblib'
HAZ={'UP':OUT/'target_general_maker_up_core_book_v1.joblib','DOWN':OUT/'target_general_maker_down_core_book_v1.joblib'}
SEED=20260820

def metrics(y,p):
 y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':len(y),'positives':int(y.sum()),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p))}

def ebm(fs,seed):return ExplainableBoostingClassifier(feature_names=fs,max_bins=96,max_interaction_bins=32,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=1600,early_stopping_rounds=80,min_samples_leaf=12,n_jobs=-2,random_state=seed)

def top(m,n=15):
 imp=list(m.term_importances()); names=list(m.term_names_); ix=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:n]; return [{'term':str(names[i]),'importance':float(imp[i])} for i in ix]

def main():
 d=pd.read_csv(CSV); c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; tids={}
 for mid in d.market_id.astype(int).unique():
  u=[];v=[]
  for r in c.execute('''select target_side,placement_first_ms from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null order by placement_first_ms''',(int(mid),)):
   (u if str(r['target_side'])=='UP' else v).append(int(r['placement_first_ms']))
  tids[int(mid)]=(u,v)
 c.close(); results={}
 for si,side in enumerate(('UP','DOWN')):
  lab='label_up_next1s' if side=='UP' else 'label_down_next1s'; ix=0 if side=='UP' else 1; q=d[d[lab].astype(int)==1].copy(); counts=[]
  for _,r in q.iterrows():
   a=tids[int(r.market_id)][ix]; cp=int(r.checkpoint_ms); counts.append(bisect.bisect_right(a,cp+1000)-bisect.bisect_right(a,cp))
  q['burst_count']=counts; q=q[q.burst_count>0].copy(); q['label_multi']=(q.burst_count>=2).astype(int)
  haz=joblib.load(HAZ[side]); hfs=list(haz['features']); q['hazard_p']=haz['model'].predict_proba(q[hfs].apply(pd.to_numeric,errors='coerce'))[:,1]
  # Runtime-safe lifecycle + placement-memory features already present in dataset. Exclude future labels and economics not needed.
  life=['last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s','maker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s','last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms','placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s','up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak']
  fs=['hazard_p']+hfs+life
  mm=q[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']); ids=mm.market_id.astype(int).tolist(); n=len(ids); a=int(n*.70); b=int(n*.85); sp={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])}; parts={k:q[q.market_id.astype(int).isin(v)].copy() for k,v in sp.items()}
  m=ebm(fs,SEED+si); m.fit(parts['train'][fs].apply(pd.to_numeric,errors='coerce'),parts['train'].label_multi.astype(int)); art=UP_ART if side=='UP' else DN_ART; joblib.dump({'version':'TARGET_MAKER_BURST_V1','side':side,'features':fs,'model':m,'trainingMarkets':sorted(sp['train']),'trainingMaxEndMs':int(parts['train'].market_end_ms.max())},art)
  rr={'rows':len(q),'markets':int(q.market_id.nunique()),'multiRate':float(q.label_multi.mean()),'meanCount':float(q.burst_count.mean()),'splitMarkets':{k:len(v) for k,v in sp.items()},'metrics':{},'topTerms':top(m),'artifact':str(art)}
  for k,x in parts.items():
   y=x.label_multi.astype(int); base=x.hazard_p.to_numpy(float); pred=m.predict_proba(x[fs].apply(pd.to_numeric,errors='coerce'))[:,1]; rr['metrics'][k]={'hazardOnlyRanking':metrics(y,base),'burstEbm':metrics(y,pred),'deltaAuc':float(metrics(y,pred)['auc']-metrics(y,base)['auc'])}
  results[side]=rr; print(json.dumps({side:{'metrics':rr['metrics'],'top':rr['topTerms'][:8]}},indent=2),flush=True)
 rep={'reportVersion':'TARGET_MAKER_BURST_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'question':'Conditional on at least one Target same-side Maker placement in next 1s, can strict-past hazard pressure + own lifecycle identify multi-placement/fast-refill burst seconds?','label':'count of high-confidence anchored same-side placement_first_ms in (checkpoint,checkpoint+1s]; multi=count>=2','results':results,'guards':['Future Target placements are labels only.','Chronological market split.','No outcome/winner input.','No hyperparameter sweep.','Do not promote unless lift over hazard-only ranking is stable on validation and test.']}; REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'report':str(REPORT)},indent=2))
if __name__=='__main__':main()
