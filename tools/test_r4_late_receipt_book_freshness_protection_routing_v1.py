from __future__ import annotations
import bisect,json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'data/research/r4_v0/hourly'
DB=ROOT/'data/public_research_archive_v1.db'
FILES={
 'FRESH24':H/'r4_queue_opportunity_hft_shadow_v1_fresh24_rows.csv',
 'UNSEEN24':H/'r4_queue_opportunity_hft_shadow_v1_unseen24_rows.csv',
 'REPLICATION3':H/'r4_queue_opportunity_hft_shadow_v1_replication3_rows.csv',
}
OUT=H/'r4_late_receipt_book_freshness_protection_routing_v1.json'
PREREG=H/'r4_late_receipt_book_freshness_protection_routing_v1_preregistered.json'
BASE=[
 'seconds_left','floor','absNet','pre_abs_payoff_gap','pre_risk_deficit',
 'predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant',
 'weak_mid_sync','weak_completion_payoff_mid','spread_ticks'
]
FULL=BASE+['queue_book_age_ms']

def hgb(seed:int):
 return HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=160,l2_regularization=3.,min_samples_leaf=25,random_state=seed)

def met(y,p):
 y=np.asarray(y,dtype=int);p=np.asarray(p,float)
 return {'n':int(len(y)),'positives':int(y.sum()),'rate':float(y.mean()) if len(y) else None,
         'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,
         'ap':float(average_precision_score(y,p)) if len(y) else None,
         'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None}

def load_public(mids):
 c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');out={}
 for i in range(0,len(mids),80):
  blk=mids[i:i+80]
  if not blk: continue
  q=','.join('?'*len(blk))
  sql=f'''select market_id,sampled_at_ms,predict_up_bid,predict_up_ask,predict_up_mid,predict_down_bid,predict_down_ask,predict_down_mid from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms'''
  for r in c.execute(sql,blk): out.setdefault(int(r['market_id']),[]).append((int(r['sampled_at_ms']),dict(r)))
 c.close();return out

def attach_future_break(d):
 parts=[]
 for mid,g in d.groupby('marketId',sort=False):
  g=g.sort_values('t').copy();ts=g.t.astype('int64').to_numpy();fl=g.floor.astype(float).to_numpy();ys=[]
  for k in range(len(g)):
   hi=np.searchsorted(ts,ts[k]+10000,side='right');fut=fl[k+1:hi]
   ys.append(np.nan if len(fut)==0 else int(np.nanmin(fut)<=0.0))
  g['y']=ys;parts.append(g)
 return pd.concat(parts,ignore_index=True) if parts else d.assign(y=np.nan)

def attach_quote(d,pub):
 vals=[]
 for z in d.itertuples(index=False):
  arr=pub.get(int(z.marketId),[])
  if not arr: vals.append(None);continue
  times=[x[0] for x in arr];j=bisect.bisect_left(times,int(z.t))-1
  if j<0: vals.append(None);continue
  t,s=arr[j];lag=int(z.t)-t
  if lag<0 or lag>1500: vals.append(None);continue
  side=str(z.weakSide).upper();bid=s['predict_up_bid'] if side=='UP' else s['predict_down_bid'];ask=s['predict_up_ask'] if side=='UP' else s['predict_down_ask'];mid=s['predict_up_mid'] if side=='UP' else s['predict_down_mid']
  if any(x is None for x in (bid,ask,mid)): vals.append(None);continue
  vals.append((lag,float(bid),float(ask),float(mid)))
 d=d.copy();d['_q']=vals;d=d[d._q.notna()].copy()
 d['public_lag_ms']=d._q.map(lambda x:x[0]);d['weak_bid']=d._q.map(lambda x:x[1]);d['weak_ask']=d._q.map(lambda x:x[2]);d['weak_mid']=d._q.map(lambda x:x[3]);d.drop(columns=['_q'],inplace=True)
 d['weak_spread']=d.weak_ask-d.weak_bid;d['weak_bid_cheapness']=d.weak_mid-d.weak_bid;d['weak_ask_premium']=d.weak_ask-d.weak_mid;d['weak_completion_payoff_bid']=1-d.weak_bid
 return d

def prepare():
    raw={k:pd.read_csv(v).replace([np.inf,-np.inf],np.nan) for k,v in FILES.items()}
    out={}
    need=['t','marketId','weakSide','seconds_left','floor','absNet','pre_abs_payoff_gap','pre_risk_deficit','predict_edge','predict_up_mid','predict_down_mid','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','queue_book_age_ms','spread_ticks']
    for k,d in raw.items():
        d=d.dropna(subset=need).copy();d['marketId']=d.marketId.astype(int);d=attach_future_break(d)
        d=d[(d.y.notna())&(d.seconds_left>0)&(d.seconds_left<=60)&(d.floor>0)&(d.queue_book_age_ms>=0)&(d.queue_book_age_ms<=1500)].copy()
        d['weak_mid_sync']=np.where(d.weakSide.astype(str).str.upper().eq('UP'),d.predict_up_mid,d.predict_down_mid)
        d['weak_completion_payoff_mid']=1.0-d.weak_mid_sync
        d=d.dropna(subset=FULL+['y']).copy();d['y']=d.y.astype(int);out[k]=d.sort_values(['marketId','t']).reset_index(drop=True)
    return out

def score_pair(mb,mq,d):
 if len(d)<20 or d.y.nunique()<2:return None
 pb=mb.predict_proba(d[BASE])[:,1];pq=mq.predict_proba(d[FULL])[:,1];b=met(d.y,pb);q=met(d.y,pq)
 return {'markets':int(d.marketId.nunique()),'rows':int(len(d)),'BASE':b,'PLUS_BOOK_FRESHNESS':q,'increment':{'deltaAuc':q['auc']-b['auc'],'deltaAp':q['ap']-b['ap'],'logLossImprovement':b['logLoss']-q['logLoss']}}

def main():
 ds=prepare();fresh=ds['FRESH24'];ordered=[int(x) for x in fresh.groupby('marketId').t.min().sort_values().index]
 cut=max(12,int(.75*len(ordered)));cut=min(cut,max(1,len(ordered)-4));tr_ids=set(ordered[:cut]);va_ids=set(ordered[cut:]);tr=fresh[fresh.marketId.isin(tr_ids)];va=fresh[fresh.marketId.isin(va_ids)]
 mb=hgb(7301).fit(tr[BASE],tr.y);mq=hgb(7302).fit(tr[FULL],tr.y);fresh_tail=score_pair(mb,mq,va)
 mb2=hgb(7311).fit(fresh[BASE],fresh.y);mq2=hgb(7312).fit(fresh[FULL],fresh.y)
 indep={k:score_pair(mb2,mq2,ds[k]) for k in ('UNSEEN24','REPLICATION3')}
 eligible=[z for z in indep.values() if z is not None and z['BASE']['positives']>=10]
 mean=lambda key:float(np.mean([z['increment'][key] for z in eligible])) if eligible else None
 support_ok=len(eligible)==2
 fresh_ok=fresh_tail is not None and fresh_tail['increment']['deltaAuc']>=-0.01
 keep=bool(support_ok and fresh_ok and all(z['increment']['deltaAuc']>=0 for z in eligible) and mean('deltaAuc')>=.015 and mean('deltaAp')>0 and mean('logLossImprovement')>0)
 if not support_ok:status='TESTED_INCONCLUSIVE'
 else:status='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'
 cov={k:{'markets':int(v.marketId.nunique()),'rows':int(len(v)),'positives':int(v.y.sum()) if len(v) else 0,'medianReceiptBookAgeMs':float(v.queue_book_age_ms.median()) if len(v) else None} for k,v in ds.items()}
 art={'version':'R4_LATE_RECEIPT_BOOK_FRESHNESS_PROTECTION_ROUTING_V1','testId':'R4_LATE_RECEIPT_BOOK_FRESHNESS_PROTECTION_ROUTING_V1_20260827_0738','status':status,'researchOnly':True,'actionAuthority':False,
      'semanticNovelty':json.loads(PREREG.read_text(encoding='utf-8'))['semanticDifferenceFromPrior'],
      'layerAssignment':json.loads(PREREG.read_text(encoding='utf-8'))['layerAssignment'],
      'label':'current realized HFT floor>0 in final 0-60s; y=1 if realized HFT floor reaches <=0 within next 10s; future HFT states label only',
      'coverage':cov,'fresh24ChronologicalTail':fresh_tail,'independentHoldouts':indep,
      'summary':{'eligibleIndependentHoldouts':len(eligible),'meanIndependentDeltaAuc':mean('deltaAuc'),'meanIndependentDeltaAp':mean('deltaAp'),'meanIndependentLogLossImprovement':mean('logLossImprovement'),'worstIndependentDeltaAuc':min([z['increment']['deltaAuc'] for z in eligible]) if eligible else None,'freshTailDeltaAuc':fresh_tail['increment']['deltaAuc'] if fresh_tail else None,'qualifies':keep},
      'fixedKeepRule':json.loads(PREREG.read_text(encoding='utf-8'))['fixedKeepRule'],
      'guards':json.loads(PREREG.read_text(encoding='utf-8'))['guards']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':cov,'freshTail':fresh_tail['increment'] if fresh_tail else None,'independent':{k:(v['increment'] if v else None) for k,v in indep.items()},'summary':art['summary']},ensure_ascii=False))
if __name__=='__main__':main()
