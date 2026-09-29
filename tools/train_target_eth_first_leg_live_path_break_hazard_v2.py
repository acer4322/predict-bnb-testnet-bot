from __future__ import annotations
import argparse,json,math,statistics,bisect,importlib.util
from pathlib import Path
from collections import defaultdict
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,brier_score_loss

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('basev1',HERE/'train_target_eth_first_leg_package_path_stability_teacher_v1.py');base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
EPS=base.EPS; GRID=base.GRID; LOOKAHEAD_MS=500; BUCKET_MS=100
FEATURES=list(base.FEATURES)+['live_age_log','first_level_depth_frac_initial','first_level_min_depth_frac_initial','first_depth_drop_count_log','time_since_last_depth_drop_log']

def path_metric(bids,asks,side,price):
 opp='DOWN' if side=='UP' else 'UP';return base.legal_metric(bids,asks,opp,price)

def sample_episode(states,e):
 ready=int(e['placementReadyMs']);fill=int(e['firstEventMs']);side=str(e['firstSide']);price=float(e['firstPrice']);path=[s for s in states if ready<=s[0]<=fill]
 if not path:return []
 deps=[];metrics=[]
 for t,b,a in path:
  sb=base.side_book(b,a,side,price);lm=path_metric(b,a,side,price)
  if sb is None or lm is None:continue
  deps.append((t,float(sb['levelDepth'] or 0.0)));metrics.append((t,float(lm['behind'])))
 if not deps or not metrics:return []
 initial=max(float(deps[0][1]),0.0);prev=float(deps[0][1]);mind=prev;drops=0;last_drop=None;out=[];seen_buckets=set();first_break=next((t for t,z in metrics if z>.5),None)
 mt=[x[0] for x in metrics];mv=[x[1] for x in metrics]
 for t,b,a in path:
  sb=base.side_book(b,a,side,price);lm=path_metric(b,a,side,price)
  if sb is None or lm is None:continue
  cur=float(sb['levelDepth'] or 0.0)
  if cur<prev-EPS:drops+=1;last_drop=int(t)
  mind=min(mind,cur);prev=cur
  if float(lm['behind'])>.5:continue
  bucket=(int(t)-ready)//BUCKET_MS
  if bucket in seen_buckets:continue
  seen_buckets.add(bucket)
  z=base.extract_features(states,int(t),side,price)
  if z is None:continue
  x,raw=z;hi=bisect.bisect_right(mt,int(t)+LOOKAHEAD_MS);lo=bisect.bisect_right(mt,int(t));future_break=any(v>.5 for v in mv[lo:hi]);extra=[math.log1p(max(0,int(t)-ready)),cur/max(initial,1.0),mind/max(initial,1.0),math.log1p(drops),math.log1p(max(0,int(t)-(last_drop if last_drop is not None else ready)))]
  xx=np.concatenate([x,np.asarray(extra,np.float32)]);out.append({'marketId':int(e['marketId']),'episodeKey':f"{int(e['marketId'])}:{ready}:{fill}:{side}:{price:.8f}",'t':int(t),'readyAt':ready,'firstFillAt':fill,'firstBreakAt':first_break,'y':int(future_break),'x':[None if not math.isfinite(float(v)) else float(v) for v in xx]})
 return out

def build_target_rows(episodes,book_db):
 eps=json.load(open(episodes,encoding='utf-8'))['episodes'];by=defaultdict(list)
 for e in eps:by[int(e['marketId'])].append(e)
 import sqlite3;c=sqlite3.connect(f'file:{Path(book_db).resolve().as_posix()}?mode=ro',uri=True);rows=[];resolved=0
 try:
  for n,(mid,ee) in enumerate(sorted(by.items()),1):
   lo=min(int(e['placementReadyMs']) for e in ee)-3500;hi=max(int(e['firstEventMs']) for e in ee)+100;states=base.load_states(c,mid,lo,hi,'source_timestamp_ms')
   if not states:continue
   for e in ee:
    rr=sample_episode(states,e)
    if rr:resolved+=1;rows.extend(rr)
   if n%50==0:print(json.dumps({'targetMarkets':n,'of':len(by),'resolvedEpisodes':resolved,'stateRows':len(rows)}),flush=True)
 finally:c.close()
 return rows,resolved,len(eps),len(by)

def split(rows):
 mids=sorted({r['marketId'] for r in rows});a=max(1,int(len(mids)*.6));b=max(a+1,int(len(mids)*.8));tr=set(mids[:a]);va=set(mids[a:b]);te=set(mids[b:]);return [r for r in rows if r['marketId'] in tr],[r for r in rows if r['marketId'] in va],[r for r in rows if r['marketId'] in te],{'trainMarkets':len(tr),'validationMarkets':len(va),'testMarkets':len(te),'trainMax':max(tr) if tr else None,'testMin':min(te) if te else None}

def matrix(rr):return np.asarray([[np.nan if v is None else float(v) for v in r['x']] for r in rr],np.float32),np.asarray([r['y'] for r in rr],int)

def metrics(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int);return {'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracyAt05':float(balanced_accuracy_score(y,pred)) if len(set(y))>1 else None,'brier':float(brier_score_loss(y,p)) if len(y) else None}

def warning_summary(rows,p):
 by=defaultdict(list)
 for r,pp in zip(rows,p):by[r['episodeKey']].append((r,float(pp)))
 breaks=[];safe=[]
 for k,z in by.items():
  z=sorted(z,key=lambda x:x[0]['t']);br=z[0][0].get('firstBreakAt');warn=next((r['t'] for r,p0 in z if p0>=.5),None);mx=max(p0 for _,p0 in z)
  if br is not None:breaks.append({'episodeKey':k,'breakAt':br,'warnAt':warn,'warningLeadMs':None if warn is None else int(br)-int(warn),'maxP':mx})
  else:safe.append({'episodeKey':k,'warnAt':warn,'maxP':mx})
 return {'breakEpisodes':len(breaks),'breakWarningRate':sum(x['warnAt'] is not None for x in breaks)/len(breaks) if breaks else None,'breakWarningLeadMedianMs':statistics.median([x['warningLeadMs'] for x in breaks if x['warningLeadMs'] is not None]) if any(x['warningLeadMs'] is not None for x in breaks) else None,'safeEpisodes':len(safe),'safeFalseWarningRate':sum(x['warnAt'] is not None for x in safe)/len(safe) if safe else None,'breakRows':breaks[:100]}

def bridge(model,offline,book_db):
 import sqlite3;d=json.load(open(offline,encoding='utf-8'));c=sqlite3.connect(f'file:{Path(book_db).resolve().as_posix()}?mode=ro',uri=True);out=[]
 try:
  for e in d['rows']:
   mid=int(e['marketId']);ready=int(e['reserveFirstSubmitAt']);fill=int(e['firstFillAt']);side=str(e['firstSide']);price=float(e['firstPrice']);states=base.load_states(c,mid,ready-3500,fill+100,'received_at_ms');fake={'marketId':mid,'placementReadyMs':ready,'firstEventMs':fill,'firstSide':side,'firstPrice':price};rr=sample_episode(states,fake)
   if not rr:continue
   X=np.asarray([[np.nan if v is None else float(v) for v in r['x']] for r in rr],np.float32);p=model.predict_proba(X)[:,1];warn=next((r['t'] for r,pp in zip(rr,p) if pp>=.5),None);out.append({'marketId':mid,'cycleIndex':int(e['cycleIndex']),'completedWithin30s':bool(e['completedWithin30sByTrace']),'maxHazard':float(max(p)),'meanHazard':float(np.mean(p)),'firstWarningAt':warn,'warningLeadToFillMs':None if warn is None else fill-int(warn),'stateRows':len(rr)})
 finally:c.close()
 y=np.asarray([0 if r['completedWithin30s'] else 1 for r in out],int);p=np.asarray([r['maxHazard'] for r in out],float);fail=[r for r in out if not r['completedWithin30s']];comp=[r for r in out if r['completedWithin30s']]
 def rate(rr,pred):return sum(pred(r) for r in rr)/len(rr) if rr else None
 return {'rows':out,'metrics':{'n':len(out),'failures':len(fail),'failureAucFromMaxHazard':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'completedMedianMaxHazard':statistics.median([r['maxHazard'] for r in comp]) if comp else None,'failedMedianMaxHazard':statistics.median([r['maxHazard'] for r in fail]) if fail else None,'failedWarning500msBeforeFillRate':rate(fail,lambda r:r['warningLeadToFillMs'] is not None and r['warningLeadToFillMs']>=500),'completedWarning500msBeforeFillRate':rate(comp,lambda r:r['warningLeadToFillMs'] is not None and r['warningLeadToFillMs']>=500)}}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--episodes',required=True);ap.add_argument('--book-db',required=True);ap.add_argument('--our-offline',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-output',required=True);a=ap.parse_args();rows,resolved,cand,mk=build_target_rows(a.episodes,a.book_db);tr,va,te,sp=split(rows);Xtr,ytr=matrix(tr);Xva,yva=matrix(va);Xte,yte=matrix(te);m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.04,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=3.0,class_weight='balanced',random_state=72).fit(Xtr,ytr);ptr=m.predict_proba(Xtr)[:,1];pva=m.predict_proba(Xva)[:,1];pte=m.predict_proba(Xte)[:,1];br=bridge(m,a.our_offline,a.book_db);joblib.dump({'version':'TARGET_ETH_FIRST_LEG_LIVE_PATH_BREAK_HAZARD_V2','model':m,'features':FEATURES,'lookaheadMs':LOOKAHEAD_MS,'bucketMs':BUCKET_MS},a.model_output);out={'version':'TARGET_ETH_FIRST_LEG_LIVE_PATH_BREAK_HAZARD_V2','researchOnly':True,'coverage':{'candidateEpisodes':cand,'candidateMarkets':mk,'resolvedEpisodes':resolved,'stateRows':len(rows)},'split':sp,'features':FEATURES,'metrics':{'train':metrics(ytr,ptr),'validation':metrics(yva,pva),'test':metrics(yte,pte)},'testWarning':warning_summary(te,pte),'ourV23Bridge':br,'modelOutput':a.model_output,'boundary':['500ms hazard horizon fixed by current 250ms entry + 250ms response HFT control cycle.','Only currently reachable states are scored; future break is offline label only.','No PnL/winner/18-unit fields.','OUR completion labels used only after Target fit for bridge evaluation.','No runtime action authority.']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'coverage':out['coverage'],'split':sp,'metrics':out['metrics'],'testWarning':{k:v for k,v in out['testWarning'].items() if k!='breakRows'},'bridge':br['metrics']},ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
