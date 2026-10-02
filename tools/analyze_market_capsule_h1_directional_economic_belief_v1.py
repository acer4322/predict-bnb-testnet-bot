from __future__ import annotations
import argparse, json, math, os
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

EPS=1e-12
PAIR_SPECS=[
 ('PASSIVE_2','PASSIVE_UP_2','PASSIVE_DOWN_2',0.0,2.0),
 ('PASSIVE_5','PASSIVE_UP_5','PASSIVE_DOWN_5',0.0,5.0),
 ('PASSIVE_10','PASSIVE_UP_10','PASSIVE_DOWN_10',0.0,10.0),
 ('ACTIVE_2','ACTIVE_UP_2','ACTIVE_DOWN_2',1.0,2.0),
]
PUBLIC=[
 'directionScore','spotReturn1sBps','spotReturn3sBps','futuresReturn1sBps','futuresReturn3sBps',
 'spotQueueImbalance','futuresQueueImbalance','spotTakerImbalance1s','futuresTakerImbalance1s',
 'spotMinusStrikeBps','chainlinkMinusStrikeBps','perpSpotBasisBps','predictUpMid','predictDownMid'
]
BASE=[
 'isActive','baseQty','preGapRatio','preFloorNorm','preUpsideNorm','preCostLog',
 'upBid','upAsk','downBid','downAsk','upPrice','downPrice','upQty','downQty'
]

def f(x):
 try:
  v=float(x)
  return v if math.isfinite(v) else math.nan
 except Exception:return math.nan

def load_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def load_needed_public(bundle, ids):
 ids=set(int(x) for x in ids); out={}
 with (Path(bundle)/'public_snapshots.jsonl').open(encoding='utf-8') as fh:
  for line in fh:
   r=json.loads(line); i=int(r['id'])
   if i in ids:
    z=json.loads(r['snapshot_json']); out[i]=z
 return out

def load_winners(bundle):
 out={}
 with (Path(bundle)/'market_results.jsonl').open(encoding='utf-8') as fh:
  for line in fh:
   r=json.loads(line); out[int(r['market_id'])]=str(r['winner']).upper()
 return out

def build_domain(seams_path, result_path, bundle):
 seams=load_json(seams_path)['rows']
 sm={str(s['seam_id']):s for s in seams}
 pub=load_needed_public(bundle,[s['public_sample_id'] for s in seams])
 winners=load_winners(bundle)
 d=load_json(result_path)
 acts=defaultdict(dict)
 for r in d['rows']:
  acts[str(r['seamId'])][str(r['action']['name'])]=r
 rows=[]
 for sid,s in sm.items():
  if sid not in acts: continue
  z=pub.get(int(s['public_sample_id']),{})
  mid=int(s['market_id']); winner=winners.get(mid)
  if winner not in {'UP','DOWN'}: continue
  den=abs(f(s.get('pre_net_cost')))+1.0
  total=max(EPS,abs(f(s.get('pre_up_shares')))+abs(f(s.get('pre_down_shares'))))
  for pair,un,dn,isact,bq in PAIR_SPECS:
   if un not in acts[sid] or dn not in acts[sid]: continue
   u,dd=acts[sid][un],acts[sid][dn]
   ua,da=u['action'],dd['action']
   uf=f(u.get('fillQtyAtEvent')); df=f(dd.get('fillQtyAtEvent'))
   # first structural event confirmed-fill contribution; non-fill qty is zero by construction
   uy=uf*((1.0 if winner=='UP' else 0.0)-f(ua.get('price')))
   dy=df*((1.0 if winner=='DOWN' else 0.0)-f(da.get('price')))
   y=uy-dy
   b=u.get('strictBookAtDecision') or {}
   row={
    'marketId':mid,'seamId':sid,'pair':pair,'y':y,'upContribution':uy,'downContribution':dy,
    'isActive':isact,'baseQty':bq,
    'preGapRatio':f(s.get('pre_share_gap'))/total,
    'preFloorNorm':f(s.get('pre_floor'))/den,
    'preUpsideNorm':f(s.get('pre_upside'))/den,
    'preCostLog':math.log1p(abs(f(s.get('pre_net_cost')))),
    'upBid':f((b.get('UP') or {}).get('bid')),'upAsk':f((b.get('UP') or {}).get('ask')),
    'downBid':f((b.get('DOWN') or {}).get('bid')),'downAsk':f((b.get('DOWN') or {}).get('ask')),
    'upPrice':f(ua.get('price')),'downPrice':f(da.get('price')),'upQty':f(ua.get('qty')),'downQty':f(da.get('qty')),
   }
   for k in PUBLIC: row[k]=f(z.get(k))
   rows.append(row)
 return rows

def matrix(rows, cols):return np.array([[r.get(c,math.nan) for c in cols] for r in rows],dtype=float)
def targets(rows):return np.array([r['y'] for r in rows],dtype=float)

def metrics(rows,y,p):
 ae=np.abs(y-p); by=defaultdict(list)
 for r,e in zip(rows,ae):by[int(r['marketId'])].append(float(e))
 mv={str(k):float(np.mean(v)) for k,v in by.items()}
 nz=np.abs(y)>1e-12
 sign=float(np.mean(np.sign(p[nz])==np.sign(y[nz]))) if np.any(nz) else None
 return {'nRows':len(rows),'markets':len(by),'mae':float(np.mean(ae)),'p90AbsError':float(np.quantile(ae,.9)),
         'nonzeroLabelRows':int(np.sum(nz)),'signAccuracyNonzero':sign,'marketMae':mv}

def model(name):
 if name=='Ridge':return make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),Ridge(alpha=1.0))
 return make_pipeline(SimpleImputer(strategy='median'),ExtraTreesRegressor(n_estimators=200,min_samples_leaf=5,random_state=20260908,n_jobs=1))

def compare(base,full):
 keys=sorted(set(base['marketMae']) & set(full['marketMae']))
 w=sum(full['marketMae'][k] < base['marketMae'][k]-1e-12 for k in keys)
 l=sum(full['marketMae'][k] > base['marketMae'][k]+1e-12 for k in keys)
 t=len(keys)-w-l
 return {'markets':len(keys),'wins':w,'losses':l,'ties':t,'improvementRate':w/len(keys) if keys else None,
         'aggregateMaeImprove':full['mae']<base['mae']-1e-12,
         'p90NotWorse':full['p90AbsError']<=base['p90AbsError']+1e-12,
         'gate':bool(keys and full['mae']<base['mae']-1e-12 and w/len(keys)>=.70 and full['p90AbsError']<=base['p90AbsError']+1e-12)}

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output',required=True); a=ap.parse_args()
 cfg={
  'A':('data/research/market_capsule_v1/informative_seams_stratified64_v2.json','data/research/market_capsule_v1/structural_event_a64_result_v1.json','data/research/market_capsule_v1/source_bundle_50_v1'),
  'B':('data/research/market_capsule_v1/holdout_b50_informative_seams_stratified64_v1.json','data/research/market_capsule_v1/structural_event_holdout_b64_result_v1.json','data/research/market_capsule_v1/source_bundle_holdout_b50_v1'),
  'C':('data/research/market_capsule_v1/holdout_c30_informative_seams_stratified36_v1.json','data/research/market_capsule_v1/structural_event_holdout_c36_result_v1.json','data/research/market_capsule_v1/source_bundle_holdout_c30_v1')}
 domains={k:build_domain(*v) for k,v in cfg.items()}
 out={'version':'MARKET_CAPSULE_H1_DIRECTIONAL_ECONOMIC_BELIEF_V1_RESULT_20260908','researchOnly':True,'runtimeAuthorityGranted':False,
      'population':{k:{'rows':len(v),'markets':len(set(r['marketId'] for r in v)),'nonzero':sum(abs(r['y'])>1e-12 for r in v)} for k,v in domains.items()},'models':{}}
 for mn in ['Ridge','ExtraTrees']:
  entry={}
  fitted={}
  for label,cols in [('BASE',BASE),('BASE_PUBLIC',BASE+PUBLIC)]:
   m=model(mn); m.fit(matrix(domains['A'],cols),targets(domains['A'])); fitted[label]=(m,cols)
   scores={}
   for dom in ['A','B','C']:
    y=targets(domains[dom]); p=m.predict(matrix(domains[dom],cols)); scores[dom]=metrics(domains[dom],y,p)
   entry[label]=scores
  entry['publicIncrement']={dom:compare(entry['BASE'][dom],entry['BASE_PUBLIC'][dom]) for dom in ['A','B','C']}
  entry['externalGate']=bool(entry['publicIncrement']['B']['gate'] and entry['publicIncrement']['C']['gate'])
  out['models'][mn]=entry
 out['h1Pass']=any(v['externalGate'] for v in out['models'].values())
 out['verdict']='PASS_H1_PUBLIC_BELIEF_INCREMENT' if out['h1Pass'] else 'FAIL_H1_PUBLIC_BELIEF_INCREMENT'
 out['guards']=['A fit only; B/C zero retrain','winner used only to construct offline realized settlement-contribution label','non-fill samples remain zero','no secondsLeft/trajectory/category/Target-current-action features','no threshold/model sweep','runtime authority remains false']
 op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'verdict':out['verdict'],'population':out['population'],'models':{k:{'externalGate':v['externalGate'],'B':v['publicIncrement']['B'],'C':v['publicIncrement']['C'],'Bbase':v['BASE']['B']['mae'],'Bfull':v['BASE_PUBLIC']['B']['mae'],'Cbase':v['BASE']['C']['mae'],'Cfull':v['BASE_PUBLIC']['C']['mae']} for k,v in out['models'].items()}},ensure_ascii=False))
if __name__=='__main__':main()
