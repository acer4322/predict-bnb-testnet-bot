from __future__ import annotations
import argparse, json, sqlite3, zlib, math, statistics, bisect
from pathlib import Path
from collections import defaultdict
import numpy as np
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score, brier_score_loss

EPS=1e-9
GRID=.01
FEATURES=[
 'first_price','first_bid','first_ask','first_spread','first_behind_ticks','first_level_depth','first_best_depth','first_top3_depth',
 'opp_bid','opp_ask','opp_spread','opp_best_depth','opp_top3_depth','opp_ceiling_headroom_ticks','bid_pair_sum','top3_side_imbalance',
 'first_bid_delta_1s_ticks','opp_bid_delta_1s_ticks','first_level_depth_delta_1s_frac','opp_best_depth_delta_1s_frac','updates_1s',
 'first_bid_delta_3s_ticks','opp_bid_delta_3s_ticks','first_level_depth_delta_3s_frac','opp_best_depth_delta_3s_frac','updates_3s'
]

def dec(b):
 if not b:return None
 try:return json.loads(zlib.decompress(b).decode('utf-8'))
 except Exception:return None

def apply_changes(book,ch):
 if not isinstance(ch,dict):return
 for key in ('bids','asks'):
  vals=ch.get(key)
  if not isinstance(vals,list):continue
  for x in vals:
   if isinstance(x,dict):
    try:p=float(x.get('price'));after=x.get('after');delta=x.get('delta')
    except Exception:continue
    if after is not None:
     try:a=float(after)
     except Exception:continue
    elif delta is not None:
     try:a=float(book[key].get(p,0.0))+float(delta)
     except Exception:continue
    else:continue
   elif isinstance(x,(list,tuple)) and len(x)>=3:
    try:p=float(x[0]);a=float(x[2])
    except Exception:continue
   else:continue
   if a<=EPS:book[key].pop(p,None)
   else:book[key][p]=a

def load_states(c,mid,start,end,clock='source_timestamp_ms'):
 qclock='source_timestamp_ms' if clock=='source_timestamp_ms' else 'received_at_ms'
 anchor=c.execute(f'select {qclock},is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and {qclock}<=? and is_checkpoint=1 order by {qclock} desc,id desc limit 1',(int(mid),int(start))).fetchone()
 lo=int(anchor[0]) if anchor else int(start)-60000
 stream=[]
 if anchor:stream.append(anchor)
 stream+=list(c.execute(f'select {qclock},is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? and {qclock}>? and {qclock}<=? order by {qclock},id',(int(mid),lo,int(end))))
 book={'bids':{},'asks':{}};out=[]
 for r in stream:
  t=int(r[0])
  if int(r[1]):book={'bids':{float(k):float(v) for k,v in (dec(r[2]) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(r[3]) or {}).items()}}
  else:apply_changes(book,dec(r[4]) or {})
  out.append((t,dict(book['bids']),dict(book['asks'])))
 return out

def side_book(bids,asks,side,level_price=None):
 if not bids or not asks:return None
 if side=='UP':
  bb=float(max(bids));ba=float(min(asks));bestd=float(bids.get(bb,0.0));top3=float(sum(bids[p] for p in sorted(bids,reverse=True)[:3]));ld=float(bids.get(round(float(level_price),12),bids.get(float(level_price),0.0))) if level_price is not None else None
 else:
  na=float(min(asks));nb=float(max(bids));bb=1.0-na;ba=1.0-nb;bestd=float(asks.get(na,0.0));top3=float(sum(asks[p] for p in sorted(asks)[:3]));np0=1.0-float(level_price) if level_price is not None else None;ld=float(asks.get(round(np0,12),asks.get(np0,0.0))) if np0 is not None else None
 return {'bid':bb,'ask':ba,'spread':max(0.0,ba-bb),'bestDepth':bestd,'top3Depth':top3,'levelDepth':ld}

def legal_metric(bids,asks,opp,first_price):
 z=side_book(bids,asks,opp)
 if z is None:return None
 ceiling=1.0-float(first_price)-0.01
 raw=min(ceiling,float(z['ask'])-0.01)
 p=math.floor((raw+1e-10)*100.0)/100.0
 if p<=0:return None
 return {'ceiling':ceiling,'legal':p,'behind':max(0.0,(float(z['bid'])-p)/GRID),'headroom':(ceiling-float(z['bid']))/GRID}

def state_at(states,t):
 if not states:return None
 ts=[x[0] for x in states];j=bisect.bisect_right(ts,int(t))-1
 return states[j] if j>=0 else None

def fdiv(a,b):return float(a)/float(b) if abs(float(b))>EPS else 0.0

def extract_features(states,t,side,price):
 now=state_at(states,t)
 if now is None:return None
 _,bids,asks=now;opp='DOWN' if side=='UP' else 'UP';a=side_book(bids,asks,side,price);o=side_book(bids,asks,opp);lm=legal_metric(bids,asks,opp,price)
 if a is None or o is None or lm is None:return None
 vals={
  'first_price':float(price),'first_bid':a['bid'],'first_ask':a['ask'],'first_spread':a['spread'],'first_behind_ticks':max(0.0,(a['bid']-float(price))/GRID),'first_level_depth':float(a['levelDepth'] or 0.0),'first_best_depth':a['bestDepth'],'first_top3_depth':a['top3Depth'],
  'opp_bid':o['bid'],'opp_ask':o['ask'],'opp_spread':o['spread'],'opp_best_depth':o['bestDepth'],'opp_top3_depth':o['top3Depth'],'opp_ceiling_headroom_ticks':lm['headroom'],'bid_pair_sum':a['bid']+o['bid'],'top3_side_imbalance':fdiv(a['top3Depth']-o['top3Depth'],a['top3Depth']+o['top3Depth']+1e-9)
 }
 ts=[x[0] for x in states]
 for sec in (1,3):
  past=state_at(states,int(t)-sec*1000)
  if past is None:
   pb=po=None
  else:
   _,bb,aa=past;pb=side_book(bb,aa,side,price);po=side_book(bb,aa,opp)
  vals[f'first_bid_delta_{sec}s_ticks']=(a['bid']-pb['bid'])/GRID if pb else np.nan
  vals[f'opp_bid_delta_{sec}s_ticks']=(o['bid']-po['bid'])/GRID if po else np.nan
  vals[f'first_level_depth_delta_{sec}s_frac']=(float(a['levelDepth'] or 0.0)-float(pb['levelDepth'] or 0.0))/max(abs(float(pb['levelDepth'] or 0.0)),1.0) if pb else np.nan
  vals[f'opp_best_depth_delta_{sec}s_frac']=(o['bestDepth']-po['bestDepth'])/max(abs(po['bestDepth']),1.0) if po else np.nan
  lo=bisect.bisect_right(ts,int(t)-sec*1000);hi=bisect.bisect_right(ts,int(t));vals[f'updates_{sec}s']=float(max(0,hi-lo))
 return np.asarray([float(vals[k]) if vals[k] is not None else np.nan for k in FEATURES],dtype=np.float32),vals

def build_target_episodes(placement_path,target_db):
 d=json.load(open(placement_path,encoding='utf-8'));prs=[r for r in d['rows'] if r.get('highConfidencePlacement') and r.get('orderHash') and r.get('placementCarrierReadyMs') is not None]
 by={ (int(r['marketId']),str(r['orderHash']).lower()):r for r in prs};mids=sorted({int(r['marketId']) for r in prs});c=sqlite3.connect(f'file:{Path(target_db).resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;eps=[]
 try:
  for n,mid in enumerate(mids,1):
   fills=[dict(r) for r in c.execute("select id,order_hash,event_ms,side,price,shares from wallet_shadow_target_events where asset='ETH' and market_id=? and role='MAKER' and quote_type='BID' and side in ('UP','DOWN') order by event_ms,id",(mid,))]
   if len(fills)<2:continue
   up=down=cost=0.0
   for i,f in enumerate(fills):
    gross=up+down;floor=min(up,down)-cost;absr=abs(up-down)/gross if gross>EPS else 0.0;fr=floor/max(cost,1.0);eligible=(gross>EPS and floor>=-EPS and absr<=.02+EPS and fr<.05-EPS)
    if eligible:
     h=str(f.get('order_hash') or '').lower();pl=by.get((mid,h));first_t=int(f['event_ms']);first_price=float(f['price']);first_side=str(f['side'])
     if pl and abs(int(pl.get('firstFillMs') or -10**18)-first_t)<=1:
      second=None
      for g in fills[i+1:]:
       dt=int(g['event_ms'])-first_t
       if dt>30000:break
       if str(g['side'])!=first_side and first_price+float(g['price'])<1.0-EPS:
        second=g;break
      if second is not None:
       eps.append({'marketId':mid,'firstEventMs':first_t,'firstSide':first_side,'firstPrice':first_price,'firstShares':float(f['shares']),'secondEventMs':int(second['event_ms']),'secondPrice':float(second['price']),'pairSum':first_price+float(second['price']),'placementReadyMs':int(pl['placementCarrierReadyMs']),'placementLeadMs':int(pl.get('placementLeadMs') or (first_t-int(pl['placementCarrierReadyMs'])) )})
    sh=float(f['shares']);px=float(f['price']);
    if f['side']=='UP':up+=sh
    else:down+=sh
    cost+=sh*px
   if n%200==0:print(json.dumps({'episodeScanMarkets':n,'of':len(mids),'episodes':len(eps)}),flush=True)
 finally:c.close()
 return eps

def enrich_target(eps,book_db):
 by=defaultdict(list)
 for e in eps:by[int(e['marketId'])].append(e)
 c=sqlite3.connect(f'file:{Path(book_db).resolve().as_posix()}?mode=ro',uri=True);rows=[]
 try:
  for n,(mid,ee) in enumerate(sorted(by.items()),1):
   lo=min(int(e['placementReadyMs']) for e in ee)-3500;hi=max(int(e['firstEventMs']) for e in ee)+500;states=load_states(c,mid,lo,hi,'source_timestamp_ms')
   for e in ee:
    feat=extract_features(states,int(e['placementReadyMs']),str(e['firstSide']),float(e['firstPrice']))
    if feat is None:continue
    x,raw=feat;opp='DOWN' if e['firstSide']=='UP' else 'UP';path=[s for s in states if int(e['placementReadyMs'])<=s[0]<=int(e['firstEventMs'])];behind=[]
    for _,b,a in path:
     lm=legal_metric(b,a,opp,float(e['firstPrice']))
     if lm is not None:behind.append(float(lm['behind']))
    if not behind:continue
    br=any(z>.5 for z in behind)
    rows.append({**e,'pathBreak':bool(br),'oppWithin1ReceiptFraction':sum(z<=1.0+EPS for z in behind)/len(behind),'firstLegRestMs':int(e['firstEventMs'])-int(e['placementReadyMs']),'features':raw,'x':[None if not math.isfinite(float(v)) else float(v) for v in x]})
   if n%100==0:print(json.dumps({'bookMarkets':n,'of':len(by),'resolved':len(rows)}),flush=True)
 finally:c.close()
 return rows

def metric_block(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
 return {'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracyAt05':float(balanced_accuracy_score(y,pred)) if len(set(y))>1 else None,'brier':float(brier_score_loss(y,p)) if len(y) else None,'meanPBreak':float(np.mean(p)) if len(p) else None}

def split_rows(rows):
 mids=sorted({int(r['marketId']) for r in rows});n=len(mids);a=max(1,int(n*.6));b=max(a+1,int(n*.8));tr=set(mids[:a]);va=set(mids[a:b]);te=set(mids[b:]);return [r for r in rows if r['marketId'] in tr],[r for r in rows if r['marketId'] in va],[r for r in rows if r['marketId'] in te],{'trainMarkets':len(tr),'validationMarkets':len(va),'testMarkets':len(te),'trainMax':max(tr) if tr else None,'validationRange':[min(va),max(va)] if va else None,'testMin':min(te) if te else None}

def matrix(rr):return np.asarray([[np.nan if v is None else float(v) for v in r['x']] for r in rr],np.float32),np.asarray([int(r['pathBreak']) for r in rr],int)

def bridge_our(model,offline_path,book_db):
 d=json.load(open(offline_path,encoding='utf-8'));c=sqlite3.connect(f'file:{Path(book_db).resolve().as_posix()}?mode=ro',uri=True);out=[]
 try:
  for r in d['rows']:
   mid=int(r['marketId']);t=int(r['reserveFirstSubmitAt']);side=str(r['firstSide']);price=float(r['firstPrice']);states=load_states(c,mid,t-3500,t+100,'received_at_ms');z=extract_features(states,t,side,price)
   if z is None:continue
   x,raw=z;p=float(model.predict_proba(x.reshape(1,-1))[0,1]);out.append({'marketId':mid,'cycleIndex':int(r['cycleIndex']),'completedWithin30s':bool(r['completedWithin30sByTrace']),'pBreakTeacher':p,'submitLegalBehindTicks':r.get('submitLegalBehindTicks'),'firstLegalBehindTicks':r.get('firstLegalBehindTicks')})
 finally:c.close()
 y=np.asarray([0 if r['completedWithin30s'] else 1 for r in out],int);p=np.asarray([r['pBreakTeacher'] for r in out],float);comp=[r['pBreakTeacher'] for r in out if r['completedWithin30s']];fail=[r['pBreakTeacher'] for r in out if not r['completedWithin30s']]
 return {'rows':out,'metrics':{'n':len(out),'failed':int(y.sum()),'failureAucFromTargetPBreak':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'completedMedianPBreak':statistics.median(comp) if comp else None,'failedMedianPBreak':statistics.median(fail) if fail else None,'completedMeanPBreak':statistics.mean(comp) if comp else None,'failedMeanPBreak':statistics.mean(fail) if fail else None}}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--placement',default='data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_full_v1.json');ap.add_argument('--target-db',default='data/target_wallet_official_v1.db');ap.add_argument('--book-db',default='data/wallet_maker_book_inference_eth5m.db');ap.add_argument('--our-offline',default='data/research/r4_v0/p0_provenance_v1/ETH_V23_FIRSTFILL_FRONTIER_OFFLINE_V1.json');ap.add_argument('--output',required=True);ap.add_argument('--model-output',required=True);a=ap.parse_args()
 eps=build_target_episodes(a.placement,a.target_db);rows=enrich_target(eps,a.book_db);tr,va,te,split=split_rows(rows);Xtr,ytr=matrix(tr);Xva,yva=matrix(va);Xte,yte=matrix(te)
 model=HistGradientBoostingClassifier(max_iter=220,learning_rate=.04,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=3.0,class_weight='balanced',random_state=71).fit(Xtr,ytr)
 pva=model.predict_proba(Xva)[:,1];pte=model.predict_proba(Xte)[:,1];ptr=model.predict_proba(Xtr)[:,1];bridge=bridge_our(model,a.our_offline,a.book_db);joblib.dump({'model':model,'features':FEATURES,'version':'TARGET_ETH_FIRST_LEG_PACKAGE_PATH_STABILITY_TEACHER_V1'},a.model_output)
 out={'version':'TARGET_ETH_FIRST_LEG_PACKAGE_PATH_STABILITY_TEACHER_V1','researchOnly':True,'episodeScan':{'candidateCheapEpisodes':len(eps),'resolvedEpisodes':len(rows),'markets':len({r['marketId'] for r in rows}),'pathBreakRate':sum(r['pathBreak'] for r in rows)/len(rows) if rows else None},'split':split,'features':FEATURES,'metrics':{'train':metric_block(ytr,ptr),'validation':metric_block(yva,pva),'test':metric_block(yte,pte)},'ourV23Bridge':bridge,'modelOutput':a.model_output,'boundary':['Target actual Maker fills + no18 high-confidence first-parent placements only.','PATH_BREAK label is structural loss of opposite passive economic-frontier reachability before first actual fill.','Winner/PnL and future rest time are absent from features.','Model fit uses Target chronology only; OUR V23 completion is bridge evaluation only, never fit.','This artifact grants no runtime authority.']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'episodeScan':out['episodeScan'],'split':split,'metrics':out['metrics'],'bridge':bridge['metrics']},ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
