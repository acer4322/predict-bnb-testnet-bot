from __future__ import annotations
import bisect, json, math, sqlite3, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.test_target_btc_eth_directional_thesis_memory_v2 import events_for,label
BASE=ROOT/'data/research/r4_v0/p0_provenance_v1'
DS=BASE/'eth_maker_placement_eventclock_v3/dataset.npz';META=BASE/'eth_maker_placement_eventclock_v3/dataset.meta.json'
PLAC=BASE/'eth_maker_placement_no18_pilot300_v1.json';BOOK=ROOT/'data/wallet_maker_book_inference_eth5m.db';OUT=BASE/'TARGET_ETH_DIRECTIONAL_THESIS_PUBLIC_PATH_V3.json'
PARAMS=dict(learning_rate=.05,max_iter=220,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=3.0,class_weight='balanced',random_state=20260902)

def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'n':int(len(y)),'positive':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1])) if len(y) else None}
def split(mids):
 ms=sorted(set(int(x) for x in mids));n=len(ms);a=max(1,int(.70*n));b=max(1,int(.15*n));
 if a+b>=n:b=max(1,n-a-1)
 return set(ms[:a]),set(ms[a:a+b]),set(ms[a+b:])
def fit(X,y,mids,cols):
 tr,va,te=split(mids);itr=np.asarray([int(m) in tr for m in mids]);iva=np.asarray([int(m) in va for m in mids]);ite=np.asarray([int(m) in te for m in mids]);m=HistGradientBoostingClassifier(**PARAMS);m.fit(X[itr][:,cols],y[itr]);return m,{'markets':{'train':len(tr),'validation':len(va),'test':len(te)},'train':met(y[itr],m.predict_proba(X[itr][:,cols])[:,1]),'validation':met(y[iva],m.predict_proba(X[iva][:,cols])[:,1]),'test':met(y[ite],m.predict_proba(X[ite][:,cols])[:,1])},ite

def placements_received(blocked):
 src=json.load(open(PLAC,encoding='utf-8'))['rows'];raw=[r for r in src if r.get('highConfidencePlacement') and int(r['marketId']) not in blocked and r.get('placementCarrierReadyMs') is not None]
 by=defaultdict(list)
 for r in raw:by[int(r['marketId'])].append(dict(r))
 c=sqlite3.connect(f'file:{BOOK.resolve().as_posix()}?mode=ro',uri=True)
 out=defaultdict(list)
 for mid,rr in by.items():
  st=list(c.execute('select source_timestamp_ms,received_at_ms from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,))); mp={}
  for s,r in st:mp[int(s)]=min(int(r),mp.get(int(s),10**30))
  for x in rr:
   sr=int(x['placementCarrierReadyMs']);rv=mp.get(sr)
   if rv is None:
    cand=[(abs(k-sr),v) for k,v in mp.items() if abs(k-sr)<=50];rv=min(cand)[1] if cand else None
   if rv is None:continue
   x['placementReceivedMs']=int(rv);out[mid].append(x)
  out[mid].sort(key=lambda z:(int(z['placementReceivedMs']),str(z.get('parentId'))))
 c.close();return out

def main():
 z=np.load(DS);meta=json.load(open(META,encoding='utf-8'));features=meta['features'];blocked=set(int(x) for x in meta.get('blocked',[]));X=z['X'].astype(float);yh=z['y_hazard'];ys=z['y_side_up'];yw=z['y_weak'];mids=z['market_id'].astype(int);times=z['timestamp_ms'].astype(np.int64)
 byix=defaultdict(list)
 for i,(m,t) in enumerate(zip(mids,times)):byix[int(m)].append((int(t),i))
 for m in byix:byix[m].sort()
 pls=placements_received(blocked);events=events_for('ETH',set(pls.keys()))
 sel=[];seen=set();audit={'placements':0,'matchedReceiptState':0,'dominantAtPlacement':0}
 for mid in sorted(pls):
  arr=byix.get(mid,[]);ats=[x[0] for x in arr];prev=-10**30
  for p in pls[mid]:
   audit['placements']+=1;pr=int(p['placementReceivedMs']);side=str(p['side']).upper();key=(mid,str(p.get('parentId') or p.get('orderHash')),pr)
   if key in seen:continue
   j=bisect.bisect_left(ats,pr)-1;chosen=None
   while j>=0 and ats[j]>=pr-500 and ats[j]>prev:
    ix=arr[j][1]
    sideok=(float(ys[ix])>=.5)==(side=='UP') if np.isfinite(ys[ix]) else False
    if int(yh[ix])==1 and sideok and np.isfinite(yw[ix]) and float(yw[ix])<.5:
     chosen=ix;break
    j-=1
   prev=pr
   if chosen is None:continue
   audit['matchedReceiptState']+=1;audit['dominantAtPlacement']+=1;seen.add(key)
   ff=int(p.get('firstFillMs') or pr);yy=label(events.get(mid,[]),ff,side);sel.append((chosen,mid,side,ff,yy,pr))
 if not sel:raise SystemExit('no matched dominant placements')
 ix=np.asarray([r[0] for r in sel],int);mid=np.asarray([r[1] for r in sel],int);side=np.asarray([r[2] for r in sel]);y=np.asarray([r[4] for r in sel],int);X0=X[ix];sidecol=(side=='UP').astype(float).reshape(-1,1);XX=np.hstack([X0,sidecol]);side_ix=XX.shape[1]-1
 path_start=features.index('update_add_qty');path_end=features.index('imbalance_d1')+1
 groups={'STATIC':list(range(path_start))+[side_ix],'STATIC_PLUS_PATH':list(range(path_end))+[side_ix],'FULL79':list(range(len(features)))+[side_ix]}
 res={};mods={};ite=None
 for n,c in groups.items():mods[n],res[n],ite=fit(XX,y,mid,c)
 # Candidate-side oriented descriptive path geometry on held-out chronology.
 ub250=features.index('up_bid_d250');ua250=features.index('up_ask_d250');ub1=features.index('up_bid_d1');ua1=features.index('up_ask_d1');im250=features.index('imbalance_d250');im1=features.index('imbalance_d1')
 orient=np.where(side=='UP',1.0,-1.0)
 cand_bid_d250=np.where(side=='UP',X0[:,ub250],-X0[:,ua250]);cand_bid_d1=np.where(side=='UP',X0[:,ub1],-X0[:,ua1]);cand_imb250=orient*X0[:,im250];cand_imb1=orient*X0[:,im1]
 def medv(v,mask):return float(np.median(v[mask])) if np.any(mask) else None
 anatomy={}
 for n,v in [('candidate_bid_d250',cand_bid_d250),('candidate_bid_d1',cand_bid_d1),('candidate_imbalance_d250',cand_imb250),('candidate_imbalance_d1',cand_imb1)]:anatomy[n]={'positiveMedian':medv(v,ite&(y==1)),'negativeMedian':medv(v,ite&(y==0))}
 rep={'version':'TARGET_ETH_DIRECTIONAL_THESIS_PUBLIC_PATH_V3','researchOnly':True,'actionAuthority':False,'preRegistration':'TARGET_ETH_DIRECTIONAL_THESIS_PUBLIC_PATH_V3_PREREGISTERED.json','coverage':{'rows':len(sel),'markets':len(set(mid.tolist())),'audit':audit},'groups':res,'testAucDeltas':{'PATH_vs_STATIC':res['STATIC_PLUS_PATH']['test']['auc']-res['STATIC']['test']['auc'],'FULL_vs_STATIC':res['FULL79']['test']['auc']-res['STATIC']['test']['auc'],'FULL_vs_PATH':res['FULL79']['test']['auc']-res['STATIC_PLUS_PATH']['test']['auc']},'pathAnatomyTest':anatomy,'guards':{'winnerUsed':False,'pnlUsed':False,'futureFeatureUsed':False,'behaviorChanged':False}}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'out':str(OUT.relative_to(ROOT)),'coverage':rep['coverage'],'test':{k:v['test'] for k,v in res.items()},'deltas':rep['testAucDeltas'],'path':anatomy},indent=2),flush=True)
if __name__=='__main__':main()
