from __future__ import annotations
import json,lzma,sys,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as sh

SRC=ROOT/'data/hft_forward_paper_v1/markets'
TAPE=ROOT/'data/execution_tape_v1/markets'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_ordinary_v2.joblib'
TRAINROWS=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_ordinary_v2_rows.csv'
PREREGS={
 'FRESH':ROOT/'data/research/r4_v0/hourly/r4_rolling_gap_owner_fresh24_preregistered.json',
 'UNSEEN':ROOT/'data/research/r4_v0/hourly/r4_strike_formation_mode_override_unseen24_preregistered.json',
 'REP3':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_shadow_replication3_preregistered.json',
}
OUT=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_hft_transfer_v2.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_hft_transfer_v2_rows.csv'

def load(mid):
 p=SRC/f'{int(mid)}_r2_hft_closed_loop_v1.json.xz'
 if not p.exists() or not (TAPE/f'{int(mid)}.json.xz').exists():return None
 try:
  with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
 except Exception:return None
 if 'R2_RESIDUAL' not in str(d.get('student') or '') or not (d.get('orderMeta') or {}):return None
 return d

def sig(r):
 return {k:r.get(k) for k in ['makerFilledShares','earlyMakerFillShares','earlySurplusFillShares','earlyFloorDamage','agedOptionFillShares','everSafe','durableBase','firstSafeMs','firstDurableMs','final','positiveDurationSec','maxFloorDrawdown','minFloor','counts','cancelReasons']}
def same(a,b):return json.dumps(a,sort_keys=True,allow_nan=True)==json.dumps(b,sort_keys=True,allow_nan=True)
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 if len(np.unique(y))<2:return {'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def train_floor_only():
 df=pd.read_csv(TRAINROWS).sort_values(['event_ms','market_id']);mids=df.groupby('market_id').event_ms.min().sort_values().index.tolist();c=max(1,int(len(mids)*.8));tr=df[df.market_id.isin(set(mids[:c]))]
 m=HistGradientBoostingClassifier(max_depth=3,max_iter=180,learning_rate=.05,min_samples_leaf=20,l2_regularization=1.0,random_state=20260829).fit(tr[['floor']],tr.floor_relapse_5s)
 return m

def main():
 full=joblib.load(MODEL)['model'];floor_only=train_floor_only();all_rows=[];cres={};all_exact=True;errors=[]
 for cname,pp in PREREGS.items():
  ids=list(map(int,json.loads(pp.read_text(encoding='utf-8'))['marketIds']));guards=[];rows=[]
  for mid in ids:
   d=load(mid)
   if d is None:errors.append({'cohort':cname,'marketId':mid,'error':'HFT_SOURCE_NOT_FOUND'});continue
   try:
    plain=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);shadow=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True);eq=same(sig(plain),sig(shadow));guards.append(eq);all_exact=all_exact and eq
    for r in shadow.get('shadowRows',[]):
     if 0<float(r.get('seconds_left') or -1)<=60 and float(r.get('floor') or 0)>=0:
      z=dict(r);z['cohort']=cname;z['abs_gap']=float(z.get('absNet') or z.get('pre_abs_payoff_gap') or 0.);rows.append(z);all_rows.append(z)
    print(json.dumps({'cohort':cname,'market':mid,'safeFinal60Rows':sum(1 for r in shadow.get('shadowRows',[]) if 0<float(r.get('seconds_left') or -1)<=60 and float(r.get('floor') or 0)>=0),'noRegression':eq}),flush=True)
   except Exception as e:errors.append({'cohort':cname,'marketId':mid,'error':f'{type(e).__name__}:{e}'})
  df=pd.DataFrame(rows)
  if df.empty:
   cres[cname]={'n':0,'noRegression':all(guards) if guards else False};continue
  df=df.replace([np.inf,-np.inf],np.nan).dropna(subset=['seconds_left','floor','abs_gap','floorRelapse5s'])
  pf=floor_only.predict_proba(df[['floor']])[:,1];pg=full.predict_proba(df[['seconds_left','floor','abs_gap']])[:,1];a=met(df.floorRelapse5s,pf);b=met(df.floorRelapse5s,pg)
  delta={}
  if 'auc'in a and 'auc'in b:delta={'auc':b['auc']-a['auc'],'ap':b['ap']-a['ap'],'logLossImprovement':a['logLoss']-b['logLoss']}
  cres[cname]={'rows':int(len(df)),'markets':int(df.marketId.nunique()),'FLOOR_ONLY':a,'FULL':b,'deltaFullVsFloor':delta,'noRegression':bool(guards) and all(guards),'marketsChecked':len(guards)}
 lifts=[v.get('deltaFullVsFloor',{}).get('auc') for v in cres.values() if 'auc' in v.get('deltaFullVsFloor',{})];fullaucs=[v.get('FULL',{}).get('auc') for v in cres.values() if 'auc' in v.get('FULL',{})]
 fixed=bool(len(lifts)==3 and len(fullaucs)==3 and all(x>.5 for x in fullaucs) and np.mean(lifts)>0 and sum(x>=0 for x in lifts)>=2 and all_exact and not errors)
 art={'version':'R4_PROTECTION_MANAGER_HOLD_HFT_TRANSFER_V2','status':'TESTED_KEEP_SIGNAL' if fixed else 'TESTED_REJECTED','researchOnly':True,'actionAuthority':False,'frozenTargetModel':str(MODEL.relative_to(ROOT)).replace('\\','/'),'cohorts':cres,'summary':{'fullAucs':fullaucs,'aucLiftsVsFloor':lifts,'meanAucLiftVsFloor':float(np.mean(lifts)) if lifts else None,'nonnegativeLiftCohorts':int(sum(x>=0 for x in lifts)) if lifts else 0,'executionNoRegressionAllExact':all_exact,'fixedRulePassed':fixed},'errors':errors,'guards':['Frozen ordinary Target model; no HFT refit.','HFT future floor relapse is scoring label only.','No MAIN/OPTION future-submit label.','No action changes.','No threshold tuning.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');pd.DataFrame(all_rows).to_csv(ROWS,index=False);print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':art['status'],'summary':art['summary'],'cohorts':cres,'errors':errors},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
