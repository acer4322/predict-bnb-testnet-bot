from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as sh
PR=ROOT/'data/research/r4_v0/hourly/r4_management_early_geometry_new_chronology_replication_v1_preregistered.json'
TARGET=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
GROUPS={'PAIR_BALANCE':['abs_gap_delta_prev','coverage_delta_prev','absnet_ratio_delta_prev'],'FLOOR_RISK':['risk_deficit_delta_prev','floor_per_gross_delta_prev']}
TL=['floor_improves_5s','absnet_reduces_5s','joint_quality_improves_5s']; HL={'floor_improves_5s':'floorImproved5s','absnet_reduces_5s':'absNetReduced5s','joint_quality_improves_5s':'jointQualityImproves5s'}
TZ=ZoneInfo('Asia/Taipei')
def load(mid):
 p=SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz'
 try:
  with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
 except Exception:return None
 return d if 'R2_RESIDUAL' in str(d.get('student') or '') and (d.get('orderMeta') or {}) else None
def sig(r):return {k:r.get(k) for k in ['makerFilledShares','earlyMakerFillShares','earlySurplusFillShares','earlyFloorDamage','agedOptionFillShares','everSafe','durableBase','firstSafeMs','firstDurableMs','final','positiveDurationSec','maxFloorDrawdown','minFloor','counts','cancelReasons']}
def same(a,b):return json.dumps(a,sort_keys=True,allow_nan=True)==json.dumps(b,sort_keys=True,allow_nan=True)
def enrich(d,mc):
 d=d.sort_values([mc,'t']).copy();g=d.groupby(mc,sort=False)
 for c in ['abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross']:d[c+'_delta_prev']=g[c].diff().fillna(0.0)
 return d
def add_labels(d):
 out=[]
 for mid,x in d.groupby('market_id',sort=False):
  x=x.sort_values('t').copy();ts=x.t.to_numpy();n=len(x);fl=x.floor_per_gross.to_numpy(float);an=x.absnet_ratio.to_numpy(float);ff=np.full(n,np.nan);aa=np.full(n,np.nan)
  for i,t in enumerate(ts):
   j=np.searchsorted(ts,t+4000,side='left')
   if j<n and ts[j]<=t+6500:ff[i]=fl[j];aa[i]=an[j]
  x['future_floor_5s']=ff;x['future_absnet_5s']=aa;x['floor_improves_5s']=(x.future_floor_5s>x.floor_per_gross+1e-9).astype(float);x['absnet_reduces_5s']=(x.future_absnet_5s<x.absnet_ratio-1e-9).astype(float);x['joint_quality_improves_5s']=((x.floor_improves_5s==1)&(x.absnet_reduces_5s==1)).astype(float);x.loc[x.future_floor_5s.isna()|x.future_absnet_5s.isna(),TL]=np.nan;out.append(x)
 return pd.concat(out,ignore_index=True)
def fit(X,y):return HistGradientBoostingClassifier(max_iter=140,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=3.0,random_state=20260827).fit(X,y)
def met(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-6,1-1e-6);return {'n':int(len(y)),'positives':int(y.sum()),'negatives':int(len(y)-y.sum()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p))}
def batch(n,start=0,count=None):
 pre=json.loads(PR.read_text());blocks=pre['cohort']['chronologicalBlocks'];name=['NEWER_A','NEWER_B'][n-1];allids=list(map(int,blocks[name]));ids=allids[start:(start+count) if count else None];rows=[];guards=[];errors=[]
 for mid in ids:
  d=load(mid)
  if d is None:errors.append({'marketId':mid,'error':'SOURCE'});continue
  try:
   a=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);b=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True);eq=same(sig(a),sig(b));guards.append({'marketId':mid,'exact':eq});rows+=b.get('managementShadowRows',[]);print(json.dumps({'market':mid,'rows':len(b.get('managementShadowRows',[])),'exact':eq}),flush=True)
  except Exception as e:errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'})
 df=pd.DataFrame(rows).replace([np.inf,-np.inf],np.nan)
 if not df.empty:df=df[(df.seconds_left>120)&(df.seconds_left<=180)&(df.build_now==1)].copy();df['jointQualityImproves5s']=((df.floorImproved5s==1)&(df.absNetReduced5s==1)).astype(int)
 suffix=f'_part{start}_{len(ids)}' if start or count else '';csv=ROOT/f'data/research/r4_v0/hourly/r4_management_early_geometry_new_chronology_replication_v1_{name.lower()}{suffix}_rows.csv';df.to_csv(csv,index=False)
 js=ROOT/f'data/research/r4_v0/hourly/r4_management_early_geometry_new_chronology_replication_v1_{name.lower()}{suffix}_extract.json';js.write_text(json.dumps({'block':name,'rows':len(df),'markets':int(df.marketId.nunique()) if len(df) else 0,'noRegression':bool(guards) and all(x['exact'] for x in guards),'guards':guards,'errors':errors,'rowsArtifact':str(csv.relative_to(ROOT)).replace('\\','/')},indent=2))
 print(json.dumps({'artifact':str(js.relative_to(ROOT)),'rows':len(df),'markets':int(df.marketId.nunique()) if len(df) else 0,'noRegression':bool(guards) and all(x['exact'] for x in guards),'errors':errors}))
def final():
 pre=json.loads(PR.read_text());td=add_labels(enrich(pd.read_csv(TARGET),'market_id'));order=td.groupby('market_id').t.min().sort_values().index.tolist();train=set(order[:int(.60*len(order))]);models={}
 for lab in TL:
  tr=td[td.market_id.isin(train)&td[lab].notna()].copy();y=tr[lab].astype(int).to_numpy();models[lab]={'BASE':fit(tr[BASE].fillna(0).to_numpy(float),y)}
  for g,cols in GROUPS.items():models[lab][g]=fit(tr[BASE+cols].fillna(0).to_numpy(float),y)
 result={'version':'R4_MANAGEMENT_EARLY_GEOMETRY_NEW_CHRONOLOGY_REPLICATION_V1','testId':pre['testId'],'createdAt':datetime.now(TZ).isoformat(),'preRegistration':str(PR.relative_to(ROOT)).replace('\\','/'),'researchOnly':True,'actionAuthority':False,'groups':{},'blocks':{}}
 for bn in ['newer_a','newer_b']:
  p=ROOT/f'data/research/r4_v0/hourly/r4_management_early_geometry_new_chronology_replication_v1_{bn}_rows.csv';d=enrich(pd.read_csv(p),'marketId');result['blocks'][bn]={'rows':len(d),'markets':int(d.marketId.nunique())};
  for g,cols in GROUPS.items():
   result['groups'].setdefault(g,{'features':cols,'comparisons':[]})
   for lab in TL:
    y=d[HL[lab]].astype(int).to_numpy();pos=int(y.sum());neg=int(len(y)-pos)
    rec={'block':bn,'target':lab,'n':len(y),'positives':pos,'negatives':neg,'eligible':pos>=8 and neg>=8}
    if rec['eligible']:
     pb=models[lab]['BASE'].predict_proba(d[BASE].fillna(0).to_numpy(float))[:,1];pg=models[lab][g].predict_proba(d[BASE+cols].fillna(0).to_numpy(float))[:,1];mb,mg=met(y,pb),met(y,pg);rec.update({'BASE':mb,'GROUP':mg,'delta':{'auc':mg['auc']-mb['auc'],'ap':mg['ap']-mb['ap'],'logLossImprovement':mb['logLoss']-mg['logLoss']}})
    result['groups'][g]['comparisons'].append(rec)
 allkeep=True;anysupportfail=False
 for g,v in result['groups'].items():
  c=[x['delta'] for x in v['comparisons'] if x['eligible']];blocks={x['block'] for x in v['comparisons'] if x['eligible']};support=len(c)>=4 and len(blocks)>=2
  if support:
   s={'eligibleComparisons':len(c),'representedBlocks':sorted(blocks),'meanDeltaAuc':float(np.mean([x['auc'] for x in c])),'meanDeltaAp':float(np.mean([x['ap'] for x in c])),'meanLogLossImprovement':float(np.mean([x['logLossImprovement'] for x in c])),'worstDeltaAuc':float(np.min([x['auc'] for x in c])),'nonNegativeAucFraction':float(np.mean([x['auc']>=0 for x in c]))};s['keep']=bool(s['meanDeltaAuc']>=.02 and s['meanDeltaAp']>0 and s['meanLogLossImprovement']>0 and s['worstDeltaAuc']>=-.02 and s['nonNegativeAucFraction']>=.75)
  else:s={'eligibleComparisons':len(c),'representedBlocks':sorted(blocks),'keep':False};anysupportfail=True
  v['supportPass']=support;v['summary']=s;allkeep=allkeep and support and s['keep']
 if anysupportfail:decision='TESTED_INCONCLUSIVE';reason='predeclared support insufficient in at least one frozen trajectory group'
 elif allkeep:decision='TESTED_KEEP_SIGNAL';reason='both frozen EARLY trajectory groups replicated on two newer independent HFT blocks'
 else:decision='TESTED_REJECTED';reason='support passed but at least one frozen EARLY trajectory group failed the predeclared portability gate'
 result['decision']=decision;result['reason']=reason;result['authority']='EARLY_MANAGEMENT_GEOMETRY_TRAJECTORY_CONTEXT_ONLY_NOT_ACTION_AUTHORITY'
 out=ROOT/'data/research/r4_v0/hourly/r4_management_early_geometry_new_chronology_replication_v1.json';out.write_text(json.dumps(result,indent=2));print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)),'decision':decision,'summaries':{k:v['summary'] for k,v in result['groups'].items()}}))
def main():
 a=argparse.ArgumentParser();a.add_argument('--batch',type=int);a.add_argument('--start',type=int,default=0);a.add_argument('--count',type=int,default=0);a.add_argument('--final',action='store_true');x=a.parse_args();final() if x.final else batch(x.batch,x.start,x.count or None)
if __name__=='__main__':main()
