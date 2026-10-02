from __future__ import annotations

import argparse, json, math
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
SEED=20260907; EPS=1e-9
DEFAULT_SEAMS=ROOT/'data/research/market_capsule_v1/informative_seams_stratified64_v2.json'
DEFAULT_FORKS=ROOT/'data/research/lan_worker_returns/decision-seam-fork-stratified64-v2/result.json'
DEFAULT_PUBLIC=ROOT/'data/research/market_capsule_v1/benchmark_50_v1/public_snapshots.parquet'
DEFAULT_OUTPUT=ROOT/'data/research/market_capsule_v1/MARKET_CAPSULE_EXECUTION_TEACHER64_V1_RESULT_20260907.json'

CORE=[
 'seconds_left','pre_floor','pre_upside','pre_abs_share_gap','gap_fraction_of_total_shares',
 'is_active','is_passive','role_repair','role_expand','role_balanced','base_qty','actual_qty','action_price',
 'side_bid','side_ask','price_minus_bid','price_minus_ask','side_bid_depth','opposite_bid_depth','side_depth_imbalance',
 'receipt_strict_spread','receipt_strict_order_count','receipt_strict_book_received_age_ms','public_age_ms'
]
PUB=[
 'direction_score_for_action_side','spot_queue_imbalance_for_action_side','futures_queue_imbalance_for_action_side',
 'spot_taker_imbalance_250ms_for_action_side','spot_taker_imbalance_1s_for_action_side',
 'futures_taker_imbalance_250ms_for_action_side','futures_taker_imbalance_1s_for_action_side',
 'spot_return_250ms_for_action_side','spot_return_1s_for_action_side','spot_return_3s_for_action_side','spot_return_5s_for_action_side',
 'futures_return_250ms_for_action_side','futures_return_1s_for_action_side','futures_return_3s_for_action_side','futures_return_5s_for_action_side',
 'spot_minus_strike_for_action_side','chainlink_minus_strike_for_action_side','spot_minus_chainlink_for_action_side',
 'perp_spot_basis_for_action_side','volatility_alert_flag'
]
FEATURES={'EXEC_CORE':CORE,'EXEC_PUBLIC':CORE+PUB}

def f(v,default=np.nan):
 try:
  x=float(v); return x if math.isfinite(x) else default
 except Exception:return default

def build(seams_path:Path,forks_path:Path,public_path:Path)->pd.DataFrame:
 s=json.load(open(seams_path,encoding='utf-8')); r=json.load(open(forks_path,encoding='utf-8'))
 sm={x['seam_id']:x for x in s['rows']}
 ids=sorted({int(x.get('public_sample_id')) for x in s['rows'] if x.get('public_sample_id') is not None})
 con=duckdb.connect(database=':memory:'); p=public_path.resolve().as_posix().replace("'","''")
 try:
  pdf=con.execute(f"select id,snapshot_json from read_parquet('{p}') where id in ({','.join(map(str,ids)) or '-1'})").fetchdf()
 finally: con.close()
 pubmap={int(x.id):json.loads(x.snapshot_json) for _,x in pdf.iterrows()}
 out=[]
 for rr in r['rows']:
  a=rr.get('action',{}); kind=a.get('kind')
  if kind in (None,'WAIT'): continue
  seam=sm[rr['seamId']]; up=f(seam['pre_up_shares'],0); dn=f(seam['pre_down_shares'],0); side=str(a['side'])
  if abs(up-dn)<=EPS: role='BALANCED'
  else:
   weak='UP' if up<dn else 'DOWN'; role='REPAIR' if side==weak else 'EXPAND'
  bid=f(seam['receipt_strict_best_bid']); ask=f(seam['receipt_strict_best_ask']); bd=f(seam['receipt_strict_bid_depth_total']); ad=f(seam['receipt_strict_ask_depth_total'])
  if side=='UP': sbid,sask,sd,od,sign=bid,ask,bd,ad,1.0
  else: sbid,sask,sd,od,sign=(1-ask if math.isfinite(ask) else np.nan),(1-bid if math.isfinite(bid) else np.nan),ad,bd,-1.0
  den=sd+od if math.isfinite(sd) and math.isfinite(od) else np.nan; imb=(sd-od)/den if math.isfinite(den) and den>EPS else np.nan
  pub=pubmap.get(int(seam['public_sample_id']),{}) if seam.get('public_sample_id') is not None else {}
  def pn(k): return sign*f(pub.get(k))
  hh={int(x['horizonMs']):x for x in rr['horizons']}; bq=f(a.get('baseQty')); aq=f(a.get('qty')); px=f(a.get('price'))
  rec={
   'market_id':int(seam['market_id']),'action_event_ms':int(seam['action_event_ms']),'seam_id':seam['seam_id'],
   'y_fill5':int(f(hh[5000]['filledQty'],0)>EPS),'y_fill60':int(f(hh[60000]['filledQty'],0)>EPS),
   'kind':kind,'role':role,'base_qty':bq,'actual_qty':aq,'action_price':px,
   'seconds_left':f(seam['seconds_left']),'pre_floor':f(seam['pre_floor']),'pre_upside':f(seam['pre_upside']),
   'pre_abs_share_gap':abs(up-dn),'gap_fraction_of_total_shares':abs(up-dn)/max(up+dn,EPS),
   'is_active':int(kind=='ACTIVE'),'is_passive':int(kind=='PASSIVE'),'role_repair':int(role=='REPAIR'),'role_expand':int(role=='EXPAND'),'role_balanced':int(role=='BALANCED'),
   'side_bid':sbid,'side_ask':sask,'price_minus_bid':px-sbid if math.isfinite(px) and math.isfinite(sbid) else np.nan,'price_minus_ask':px-sask if math.isfinite(px) and math.isfinite(sask) else np.nan,
   'side_bid_depth':sd,'opposite_bid_depth':od,'side_depth_imbalance':imb,'receipt_strict_spread':f(seam['receipt_strict_spread']),
   'receipt_strict_order_count':f(seam['receipt_strict_order_count']),'receipt_strict_book_received_age_ms':f(seam['receipt_strict_book_received_age_ms']),'public_age_ms':f(seam['public_age_ms']),
   'direction_score_for_action_side':pn('directionScore'),'spot_queue_imbalance_for_action_side':pn('spotQueueImbalance'),'futures_queue_imbalance_for_action_side':pn('futuresQueueImbalance'),
   'spot_taker_imbalance_250ms_for_action_side':pn('spotTakerImbalance250ms'),'spot_taker_imbalance_1s_for_action_side':pn('spotTakerImbalance1s'),
   'futures_taker_imbalance_250ms_for_action_side':pn('futuresTakerImbalance250ms'),'futures_taker_imbalance_1s_for_action_side':pn('futuresTakerImbalance1s'),
   'spot_return_250ms_for_action_side':pn('spotReturn250msBps'),'spot_return_1s_for_action_side':pn('spotReturn1sBps'),'spot_return_3s_for_action_side':pn('spotReturn3sBps'),'spot_return_5s_for_action_side':pn('spotReturn5sBps'),
   'futures_return_250ms_for_action_side':pn('futuresReturn250msBps'),'futures_return_1s_for_action_side':pn('futuresReturn1sBps'),'futures_return_3s_for_action_side':pn('futuresReturn3sBps'),'futures_return_5s_for_action_side':pn('futuresReturn5sBps'),
   'spot_minus_strike_for_action_side':pn('spotMinusStrikeBps'),'chainlink_minus_strike_for_action_side':pn('chainlinkMinusStrikeBps'),'spot_minus_chainlink_for_action_side':pn('spotMinusChainlinkBps'),'perp_spot_basis_for_action_side':pn('perpSpotBasisBps'),
   'volatility_alert_flag':0.0 if str(pub.get('volatilityAlert','NORMAL'))=='NORMAL' else 1.0,
  }
  out.append(rec)
 return pd.DataFrame(out)

def make(name):
 if name=='LOGISTIC': return Pipeline([('impute',SimpleImputer(strategy='median')),('scale',StandardScaler()),('model',LogisticRegression(C=1,max_iter=1000,random_state=SEED))])
 return Pipeline([('impute',SimpleImputer(strategy='median')),('model',ExtraTreesClassifier(n_estimators=200,min_samples_leaf=5,class_weight='balanced',random_state=SEED,n_jobs=1))])
def ll(y,p):
 p=np.clip(np.asarray(p,float),1e-6,1-1e-6); y=np.asarray(y,int); return float(np.mean(-(y*np.log(p)+(1-y)*np.log(1-p))))
def auc(y,p): return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--seams',type=Path,default=DEFAULT_SEAMS); ap.add_argument('--forks',type=Path,default=DEFAULT_FORKS); ap.add_argument('--public',type=Path,default=DEFAULT_PUBLIC); ap.add_argument('--output',type=Path,default=DEFAULT_OUTPUT); ns=ap.parse_args()
 df=build(ns.seams,ns.forks,ns.public); mo=df.groupby('market_id')['action_event_ms'].min().sort_values().index.tolist(); folds=[(18,28),(28,38),(38,48)]
 store=defaultdict(list); fold_metrics=[]
 for trn,end in folds:
  trm=set(mo[:trn]); tem=set(mo[trn:end]); tr=df[df.market_id.isin(trm)].copy(); te=df[df.market_id.isin(tem)].copy()
  for head,ycol in [('FILL5','y_fill5'),('FILL60','y_fill60')]:
   # fair empirical baseline keyed by kind × role × base qty
   glob=float(np.clip(tr[ycol].mean(),1e-6,1-1e-6)); priors={}
   for k,g in tr.groupby(['kind','role','base_qty']): priors[k]=float(np.clip(g[ycol].mean(),1e-6,1-1e-6))
   bp=np.array([priors.get((x.kind,x.role,x.base_qty),glob) for _,x in te.iterrows()]); y=te[ycol].to_numpy(int)
   fold_metrics.append({'trainMarkets':trn,'testMarkets':len(tem),'head':head,'cell':'ROUTE_ROLE_QTY_PRIOR','logloss':ll(y,bp),'auc':auc(y,bp),'brier':float(brier_score_loss(y,bp))})
   for idx,yy,pp in zip(te.index.tolist(),y.tolist(),bp.tolist()): store[(head,'BASE','BASE')].append({'idx':int(idx),'marketId':int(te.loc[idx,'market_id']),'y':int(yy),'p':float(pp),'trainMarkets':trn})
   for fs,feats in FEATURES.items():
    Xtr=tr[feats].replace([np.inf,-np.inf],np.nan); Xte=te[feats].replace([np.inf,-np.inf],np.nan)
    for mn in ('LOGISTIC','EXTRATREES'):
     m=make(mn); m.fit(Xtr,tr[ycol].to_numpy(int)); p=m.predict_proba(Xte)[:,1]
     fold_metrics.append({'trainMarkets':trn,'testMarkets':len(tem),'head':head,'cell':f'{fs}__{mn}','logloss':ll(y,p),'auc':auc(y,p),'brier':float(brier_score_loss(y,p))})
     for idx,yy,pp in zip(te.index.tolist(),y.tolist(),p.tolist()): store[(head,fs,mn)].append({'idx':int(idx),'marketId':int(te.loc[idx,'market_id']),'y':int(yy),'p':float(pp),'trainMarkets':trn})
 agg={}; headpass={}
 for head in ('FILL5','FILL60'):
  br={(r['idx'],r['trainMarkets']):r for r in store[(head,'BASE','BASE')]}; passany=False
  for fs in FEATURES:
   for mn in ('LOGISTIC','EXTRATREES'):
    rows=store[(head,fs,mn)]; y=np.array([r['y'] for r in rows]); p=np.array([r['p'] for r in rows]); bp=np.array([br[(r['idx'],r['trainMarkets'])]['p'] for r in rows]); bym=defaultdict(list)
    for i,r in enumerate(rows): bym[r['marketId']].append(i)
    imp=0; ds=[]
    for mid,ix in bym.items(): a=ll(y[ix],p[ix]); b=ll(y[ix],bp[ix]); imp+=int(a<b-1e-12); ds.append(b-a)
    gain=ll(y,bp)-ll(y,p); rate=imp/max(1,len(bym)); key=f'{head}__{fs}__{mn}'
    agg[key]={'rows':len(rows),'markets':len(bym),'auc':auc(y,p),'logloss':ll(y,p),'baselineLogloss':ll(y,bp),'loglossGainVsBaseline':gain,'brier':float(brier_score_loss(y,p)),'marketImprovedVsBaseline':imp,'marketImprovementRate':rate,'medianPerMarketGain':float(np.median(ds)),'passes70pct':bool(gain>0 and rate>=.70)}; passany|=agg[key]['passes70pct']
  headpass[head]=bool(passany)
 pubinc={}
 for head in ('FILL5','FILL60'):
  for mn in ('LOGISTIC','EXTRATREES'):
   a={(r['idx'],r['trainMarkets']):r for r in store[(head,'EXEC_CORE',mn)]}; b={(r['idx'],r['trainMarkets']):r for r in store[(head,'EXEC_PUBLIC',mn)]}; keys=sorted(set(a)&set(b)); y=np.array([a[k]['y'] for k in keys]); pa=np.array([a[k]['p'] for k in keys]); pb=np.array([b[k]['p'] for k in keys]); bym=defaultdict(list)
   for j,k in enumerate(keys): bym[a[k]['marketId']].append(j)
   imp=0; ds=[]
   for mid,ix in bym.items(): la=ll(y[ix],pa[ix]); lb=ll(y[ix],pb[ix]); imp+=int(lb<la-1e-12); ds.append(la-lb)
   gain=ll(y,pa)-ll(y,pb); rate=imp/max(1,len(bym)); pubinc[f'{head}__{mn}']={'aggregateLoglossGain':gain,'marketImproved':imp,'markets':len(bym),'marketImprovementRate':rate,'medianPerMarketGain':float(np.median(ds)),'passes70pct':bool(gain>0 and rate>=.70)}
 payload={'version':'MARKET_CAPSULE_EXECUTION_TEACHER64_V1_RESULT_20260907','researchOnly':True,'rows':len(df),'uniqueMarkets':len(mo),'labelRates':{'fill5':float(df.y_fill5.mean()),'fill60':float(df.y_fill60.mean())},'featureLeakAudit':{'samplingCategoryInFeatures':False,'targetCurrentActionInFeatures':False,'winnerInFeatures':False,'futureStateInFeatures':False},'foldMetrics':fold_metrics,'aggregate':agg,'publicIncrement':pubinc,'developmentGate':{'fill5Learnable':headpass['FILL5'],'fill60Learnable':headpass['FILL60'],'bothHorizonsLearnable':bool(headpass['FILL5'] and headpass['FILL60']),'runtimeAuthorityGranted':False}}
 ns.output.parent.mkdir(parents=True,exist_ok=True); ns.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'ok':True,'output':str(ns.output),'rows':len(df),'uniqueMarkets':len(mo),'labelRates':payload['labelRates'],'aggregate':agg,'publicIncrement':pubinc,'developmentGate':payload['developmentGate']},indent=2,ensure_ascii=False)); return 0
if __name__=='__main__': raise SystemExit(main())
