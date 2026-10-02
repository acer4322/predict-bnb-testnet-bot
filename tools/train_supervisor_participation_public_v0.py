from __future__ import annotations
import json,sqlite3,math,sys
from pathlib import Path
import joblib,numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,f1_score,log_loss,brier_score_loss
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_target_maker_taker_coordination_big_v1 as coord
BOOK=ROOT/'data'/'wallet_maker_book_inference.db';TARGET=ROOT/'data'/'target_wallet_official_v1.db';OUT=ROOT/'data'/'research'/'supervisor_curriculum_v0';CSV=OUT/'participation_public_v0_states.csv';REPORT=OUT/'participation_public_v0_report.json';ART=OUT/'participation_public_v0.joblib'
MIN_END=1786903500000

def fstats(xs,prefix):
 z=np.asarray([float(x) for x in xs if x is not None and math.isfinite(float(x))],float)
 if not len(z):return {f'{prefix}_{k}':math.nan for k in ('mean','std','min','max','p10','p90','first','last','delta')}
 return {f'{prefix}_mean':float(z.mean()),f'{prefix}_std':float(z.std()),f'{prefix}_min':float(z.min()),f'{prefix}_max':float(z.max()),f'{prefix}_p10':float(np.quantile(z,.1)),f'{prefix}_p90':float(np.quantile(z,.9)),f'{prefix}_first':float(z[0]),f'{prefix}_last':float(z[-1]),f'{prefix}_delta':float(z[-1]-z[0])}
def weights(y):
 c=y.value_counts();n=len(y);mp={k:math.sqrt(n/max(1,int(v))) for k,v in c.items()};w=y.map(mp).astype(float).to_numpy();return w/w.mean()
def met(m,x,fs):
 y=x.label_participate.astype(int).to_numpy();p=m.predict_proba(x[fs].apply(pd.to_numeric,errors='coerce'))[:,1];pred=(p>=.5).astype(int);return {'n':len(x),'positiveRate':float(y.mean()),'predRate':float(pred.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'f1':float(f1_score(y,pred,zero_division=0)),'logLoss':float(log_loss(y,np.column_stack([1-p,p]),labels=[0,1])),'brier':float(brier_score_loss(y,p))}
def main():
 OUT.mkdir(parents=True,exist_ok=True);b=sqlite3.connect(f'file:{BOOK.resolve().as_posix()}?mode=ro',uri=True);b.row_factory=sqlite3.Row;b.execute('pragma query_only=on');t=sqlite3.connect(f'file:{TARGET.resolve().as_posix()}?mode=ro',uri=True);t.row_factory=sqlite3.Row;t.execute('pragma query_only=on');rows=[]
 try:
  metas={int(r['market_id']):dict(r) for r in b.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms>=?',(MIN_END,))}
  seen={int(r['market_id']) for r in t.execute("select market_id from target_markets where asset='BTC' and window_end_ms>=?",(MIN_END,))}
  mids=sorted(set(metas)&seen,key=lambda m:int(metas[m]['window_end_ms']))
  for j,m in enumerate(mids,1):
   we=int(metas[m]['window_end_ms']);start=we-300000;end=start+60000
   cps=list(b.execute('select source_timestamp_ms,native_bids_z,native_asks_z from maker_book_inference_updates where market_id=? and is_checkpoint=1 and source_timestamp_ms between ? and ? order by source_timestamp_ms,id',(m,start,end)))
   if len(cps)<2:continue
   midsx=[];spreads=[];bd=[];ad=[];b3=[];a3=[];imb=[];times=[]
   for r in cps:
    bids={float(k):float(v) for k,v in (coord.dec(r['native_bids_z']) or {}).items()};asks={float(k):float(v) for k,v in (coord.dec(r['native_asks_z']) or {}).items()}
    if not bids or not asks:continue
    bp=max(bids);ap=min(asks);bv=float(bids[bp]);av=float(asks[ap]);sb=sorted(bids,reverse=True)[:3];sa=sorted(asks)[:3];midsx.append((bp+ap)/2);spreads.append(ap-bp);bd.append(bv);ad.append(av);b3.append(sum(bids[x] for x in sb));a3.append(sum(asks[x] for x in sa));imb.append((bv-av)/(bv+av) if bv+av>0 else 0.0);times.append(int(r['source_timestamp_ms']))
   if len(midsx)<2:continue
   update_n=int(b.execute('select count(*) from maker_book_inference_updates where market_id=? and source_timestamp_ms between ? and ?',(m,start,end)).fetchone()[0]);ev_n=int(t.execute("select count(*) from wallet_shadow_target_events where asset='BTC' and quote_type='BID' and market_id=?",(m,)).fetchone()[0]);maker_n=int(t.execute("select count(*) from target_parent_orders where asset='BTC' and role='MAKER' and quote_type='BID' and market_id=?",(m,)).fetchone()[0]);taker_n=int(t.execute("select count(*) from target_parent_orders where asset='BTC' and role='TAKER' and quote_type='BID' and market_id=?",(m,)).fetchone()[0]);r={'market_id':m,'market_end_ms':we,'label_participate':int(ev_n>0),'teacher_event_count':ev_n,'teacher_maker_parents':maker_n,'teacher_taker_parents':taker_n,'early_checkpoint_count':len(midsx),'early_update_count':update_n,'early_first_checkpoint_delay_ms':times[0]-start,'early_last_checkpoint_ms':times[-1]-start};
   for xs,p in [(midsx,'mid'),(spreads,'spread'),(bd,'bid_depth'),(ad,'ask_depth'),(b3,'bid_top3'),(a3,'ask_top3'),(imb,'top_imbalance')]:r.update(fstats(xs,p))
   r['mid_range']=max(midsx)-min(midsx);r['mid_abs_delta']=abs(midsx[-1]-midsx[0]);r['depth_ratio_mean']=float(np.mean(np.asarray(bd)/(np.asarray(ad)+1e-9)));rows.append(r)
   if j%100==0:print(json.dumps({'progress':j,'total':len(mids),'rows':len(rows)}),flush=True)
 finally:b.close();t.close()
 d=pd.DataFrame(rows).sort_values(['market_end_ms','market_id']).reset_index(drop=True);d.to_csv(CSV,index=False);ids=d.market_id.astype(int).tolist();a=int(len(ids)*.70);c=int(len(ids)*.85);tr=d.iloc[:a].copy();va=d.iloc[a:c].copy();te=d.iloc[c:].copy();exclude={'market_id','market_end_ms','label_participate','teacher_event_count','teacher_maker_parents','teacher_taker_parents'};fs=[x for x in d.columns if x not in exclude];m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=100,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=1.0,early_stopping=True,validation_fraction=.12,n_iter_no_change=15,random_state=20260820);y=tr.label_participate.astype(int);m.fit(tr[fs].apply(pd.to_numeric,errors='coerce'),y,sample_weight=weights(y));rep={'reportVersion':'SUPERVISOR_PARTICIPATION_PUBLIC_V0','researchOnly':True,'question':'Can the first 60s of public book context predict whether Target participates in an ordinary BTC market at all?','source':{'markets':len(d),'minEndMs':int(d.market_end_ms.min()),'maxEndMs':int(d.market_end_ms.max()),'labelCounts':d.label_participate.value_counts().to_dict(),'captureDefinition':'target_markets row exists and public book checkpoints exist; PARTICIPATE iff wallet_shadow_target_events has BTC BID event in that market'},'featureContract':'Only public book observations from market open through +60s. No Target action/state, winner, or later market data in features.','splitMarkets':{'train':len(tr),'validation':len(va),'test':len(te)},'results':{'validation':met(m,va,fs),'test':met(m,te,fs)},'features':fs,'artifact':str(ART),'states':str(CSV),'interpretationRule':'V0 is evidence for a prerequisite Participation context only if chronological val/test ranking is meaningfully above chance. It is not a runtime policy or proof that every no-event market is strategic skip.','guards':['No winner/PnL.','No 2026-08-16 by cutoff.','No Target labels in features.','No runtime changes.']};joblib.dump({'version':'SUPERVISOR_PARTICIPATION_PUBLIC_V0','researchOnly':True,'runtimePromotion':False,'model':m,'features':fs,'trainingMarkets':tr.market_id.astype(int).tolist()},ART);REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
