from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'data/research/r4_v0/hourly'
FILES={
 'FRESH24':H/'r4_queue_opportunity_hft_shadow_v1_fresh24_rows.csv',
 'UNSEEN24':H/'r4_queue_opportunity_hft_shadow_v1_unseen24_rows.csv',
 'REPLICATION3':H/'r4_queue_opportunity_hft_shadow_v1_replication3_rows.csv',
}
OUT=H/'r4_late_queue_cross_readiness_context_v1.json'
BASE=['seconds_left','floor','absNet','pre_abs_payoff_gap','pre_risk_deficit','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','weak_mid_sync','weak_completion_payoff_mid','spread_ticks']
FULL=BASE+['queue_opportunity_score']

def hgb(seed:int):
 return HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,min_samples_leaf=25,random_state=seed)

def met(y,p):
 y=np.asarray(y,dtype=int);p=np.asarray(p,float)
 return {'n':int(len(y)),'positives':int(y.sum()),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(y) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None}

def attach_durable_cross(d):
 parts=[]
 for mid,g in d.groupby('marketId',sort=False):
  g=g.sort_values('t').copy();ts=g.t.astype('int64').to_numpy();fl=g.floor.astype(float).to_numpy();ys=[]
  for k in range(len(g)):
   hi=np.searchsorted(ts,ts[k]+5000,side='right')
   if hi<=k+1: ys.append(np.nan);continue
   fut=fl[k+1:hi];cross_idx=np.flatnonzero(fut>=0.0)
   if len(cross_idx)==0: ys.append(0);continue
   j=int(cross_idx[0]);ys.append(int(np.all(fut[j:]>=0.0)))
  g['y']=ys;parts.append(g)
 return pd.concat(parts,ignore_index=True) if parts else d.assign(y=np.nan)

def prepare():
 out={};need=['t','marketId','weakSide','seconds_left','floor','absNet','pre_abs_payoff_gap','pre_risk_deficit','predict_edge','predict_up_mid','predict_down_mid','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','queue_opportunity_score','queue_book_age_ms','spread_ticks']
 for k,p in FILES.items():
  d=pd.read_csv(p).replace([np.inf,-np.inf],np.nan).dropna(subset=need).copy();d['marketId']=d.marketId.astype(int);d=attach_durable_cross(d)
  d=d[(d.y.notna())&(d.seconds_left>0)&(d.seconds_left<=60)&(d.floor<0)&(d.queue_book_age_ms>=0)&(d.queue_book_age_ms<=1500)].copy()
  d['weak_mid_sync']=np.where(d.weakSide.astype(str).str.upper().eq('UP'),d.predict_up_mid,d.predict_down_mid);d['weak_completion_payoff_mid']=1.0-d.weak_mid_sync
  d=d.dropna(subset=FULL+['y']).copy();d['y']=d.y.astype(int);out[k]=d.sort_values(['marketId','t']).reset_index(drop=True)
 return out

def score_pair(mb,mq,d):
 if len(d)<20 or d.y.nunique()<2:return None
 pb=mb.predict_proba(d[BASE])[:,1];pq=mq.predict_proba(d[FULL])[:,1];b=met(d.y,pb);q=met(d.y,pq)
 return {'markets':int(d.marketId.nunique()),'rows':int(len(d)),'BASE':b,'PLUS_QUEUE_BELIEF':q,'increment':{'deltaAuc':q['auc']-b['auc'],'deltaAp':q['ap']-b['ap'],'logLossImprovement':b['logLoss']-q['logLoss']}}

def main():
 ds=prepare();fresh=ds['FRESH24'];ordered=[int(x) for x in fresh.groupby('marketId').t.min().sort_values().index]
 cut=max(1,int(.75*len(ordered))) if ordered else 0;cut=min(cut,max(0,len(ordered)-1));tr_ids=set(ordered[:cut]);va_ids=set(ordered[cut:]);tr=fresh[fresh.marketId.isin(tr_ids)];va=fresh[fresh.marketId.isin(va_ids)]
 cov={k:{'markets':int(v.marketId.nunique()),'rows':int(len(v)),'positives':int(v.y.sum()) if len(v) else 0,'positiveRate':float(v.y.mean()) if len(v) else None,'medianReceiptBookAgeMs':float(v.queue_book_age_ms.median()) if len(v) else None} for k,v in ds.items()}
 split={'freshTrainMarkets':len(tr_ids),'freshTrainRows':int(len(tr)),'freshTrainPositives':int(tr.y.sum()) if len(tr) else 0,'freshTrainClasses':int(tr.y.nunique()) if len(tr) else 0,'freshTailMarkets':len(va_ids),'freshTailRows':int(len(va)),'freshTailPositives':int(va.y.sum()) if len(va) else 0,'freshTailClasses':int(va.y.nunique()) if len(va) else 0}
 fresh_tail=None;indep={'UNSEEN24':None,'REPLICATION3':None};support_reason=None
 if len(tr)>=20 and tr.y.nunique()>=2:
  mb=hgb(9301).fit(tr[BASE],tr.y);mq=hgb(9302).fit(tr[FULL],tr.y);fresh_tail=score_pair(mb,mq,va)
  if fresh.y.nunique()>=2:
   mb2=hgb(9311).fit(fresh[BASE],fresh.y);mq2=hgb(9312).fit(fresh[FULL],fresh.y);indep={k:score_pair(mb2,mq2,ds[k]) for k in ('UNSEEN24','REPLICATION3')}
 else:
  support_reason='FRESH24_CHRONOLOGICAL_TRAINING_ONE_CLASS_OR_TOO_SMALL_BEFORE_MODEL_FIT'
 eligible=[z for z in indep.values() if z is not None and z['BASE']['positives']>=20]
 mean=lambda key:float(np.mean([z['increment'][key] for z in eligible])) if eligible else None
 support_ok=(support_reason is None and len(eligible)==2);fresh_ok=fresh_tail is not None and fresh_tail['increment']['deltaAuc']>=-0.01
 keep=bool(support_ok and fresh_ok and all(z['increment']['deltaAuc']>=0 for z in eligible) and mean('deltaAuc')>=.015 and mean('deltaAp')>0 and mean('logLossImprovement')>0)
 status='TESTED_INCONCLUSIVE' if not support_ok else ('TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED')
 art={'version':'R4_LATE_QUEUE_CROSS_READINESS_CONTEXT_V1','testId':'R4_LATE_QUEUE_CROSS_READINESS_CONTEXT_V1_20260827_0938','status':status,'researchOnly':True,'actionAuthority':False,'semanticNovelty':'First final-60s negative-floor CROSS_READINESS test of frozen receipt-clock queue_opportunity BELIEF on realistic HFT trajectories. Prior queue late test targeted positive-floor break risk; prior negative-floor cross-readiness test added quote economics rather than queue opportunity.','layerAssignment':{'receiptClockDepthChurn':'INFORMATION','queueOpportunityScore':'BELIEF_EXECUTION_REACHABILITY_CONTEXT','synchronizedQuoteContext':'INFORMATION','predictStrike':'BELIEF_CONTEXT','portfolioPayoffFloorGeometry':'LOGIC_STATE_CONTEXT','output':'LATE_CROSS_READINESS_CONTEXT_ONLY_NOT_ACTION_AUTHORITY'},'label':'current realized HFT floor<0 in final 0-60s; y=1 iff realized floor reaches >=0 within next 5s and stays >=0 for all subsequent observed checkpoints through current+5s; future HFT states offline label only','coverage':cov,'chronologicalSplitSupport':split,'supportFailureReason':support_reason,'fresh24ChronologicalTail':fresh_tail,'independentHoldouts':indep,'summary':{'eligibleIndependentHoldouts':len(eligible),'meanIndependentDeltaAuc':mean('deltaAuc'),'meanIndependentDeltaAp':mean('deltaAp'),'meanIndependentLogLossImprovement':mean('logLossImprovement'),'worstIndependentDeltaAuc':min([z['increment']['deltaAuc'] for z in eligible]) if eligible else None,'freshTailDeltaAuc':fresh_tail['increment']['deltaAuc'] if fresh_tail else None,'qualifies':keep},'fixedKeepRule':'KEEP_SIGNAL only if both independent holdouts have >=20 positive labels, deltaAUC>=0 in both, mean independent deltaAUC>=0.015, mean deltaAP>0, mean log-loss improvement>0, and FRESH24 chronological-tail deltaAUC>=-0.01. Insufficient support => INCONCLUSIVE; otherwise failure => REJECTED.','guards':['no threshold/model/hyperparameter sweep','frozen queue_opportunity score only','receipt-clock queue state only','future HFT trajectory offline label only','no dream fill','no Echtgeld fit/ingest','no Formation PREPARE authority','no order/size/owner/MPQ/action authority','live R3-S/R3.1/8781 untouched']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':cov,'splitSupport':split,'supportFailureReason':support_reason,'freshTail':fresh_tail['increment'] if fresh_tail else None,'independent':{k:(v['increment'] if v else None) for k,v in indep.items()},'summary':art['summary']},ensure_ascii=False))
if __name__=='__main__':main()
