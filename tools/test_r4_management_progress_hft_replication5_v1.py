from __future__ import annotations
import json,lzma,sys,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
from sklearn.ensemble import HistGradientBoostingClassifier
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as sh
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_progress_hft_replication5_preregistered.json'
STACK=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'
TARGET=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_progress_hft_replication5_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_progress_hft_replication5_v1_rows.csv'
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
FULL=BASE+PROG
OUTCOMES=['futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']
def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)

def load(mid):
 p=SRC/f'{int(mid)}_r2_hft_closed_loop_v1.json.xz'
 if not p.exists():return None
 try:
  with lzma.open(p,'rt',encoding='utf-8') as fh:d=json.load(fh)
 except Exception:return None
 if 'R2_RESIDUAL' not in str(d.get('student') or '') or not (d.get('orderMeta') or {}):return None
 if not (ROOT/'data/execution_tape_v1/markets'/f'{int(mid)}.json.xz').exists():return None
 return d

def result_signature(r):
 return {k:r.get(k) for k in ['makerFilledShares','earlyMakerFillShares','earlySurplusFillShares','earlyFloorDamage','agedOptionFillShares','everSafe','durableBase','firstSafeMs','firstDurableMs','final','positiveDurationSec','maxFloorDrawdown','minFloor','counts','cancelReasons']}
def same(a,b):
 return json.dumps(a,sort_keys=True,allow_nan=True)==json.dumps(b,sort_keys=True,allow_nan=True)
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
 pre=json.loads(PREREG.read_text(encoding='utf-8'));ids=list(map(int,pre['marketIds']));stack=joblib.load(STACK)
 td=pd.read_csv(TARGET).replace([np.inf,-np.inf],np.nan).dropna(subset=FULL).copy();td=td[(td.seconds_left>=60)&(td.seconds_left<=300)].sort_values(['market_id','t']);ms=td.groupby('market_id').t.min().sort_values().index.astype(int).tolist();tr=td[(td.market_id.isin(ms[:240]))&(td.build_now==1)].copy();m0b=hgb(26082801).fit(tr[BASE],tr.continue_weak_5s.astype(int));m0=stack['M0_model']
 rows=[];guards=[];errors=[]
 for mid in ids:
  d=load(mid)
  if d is None:errors.append({'marketId':mid,'error':'HFT_SOURCE_NOT_FOUND'});continue
  try:
   plain=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);shadow=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True);eq=same(result_signature(plain),result_signature(shadow));guards.append({'marketId':mid,'exactNoRegression':eq});rows.extend(shadow.get('managementShadowRows',[]));print(json.dumps({'market':mid,'managementRows':len(shadow.get('managementShadowRows',[])),'noRegression':eq}),flush=True)
  except Exception as e:errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'})
 df=pd.DataFrame(rows).replace([np.inf,-np.inf],np.nan)
 if df.empty:raise SystemExit('no management rows')
 df=df[(df.seconds_left>=60)&(df.seconds_left<180)&(df.build_now==1)].dropna(subset=FULL).copy();df['p_base']=m0b.predict_proba(df[BASE])[:,1];df['p_progress']=m0.predict_proba(df[FULL])[:,1]
 metrics={};all_pass=True
 for lab in OUTCOMES:
  a=met(df[lab],df.p_base);r=met(df[lab],df.p_progress);delta={'auc':r['auc']-a['auc'],'ap':r['ap']-a['ap'],'logLossImprovement':a['logLoss']-r['logLoss']};ok=all(v>=-1e-12 for v in delta.values());all_pass=all_pass and ok;metrics[lab]={'BASE':a,'PROGRESS':r,'delta':delta,'passesFixedRule':ok}
 no_reg=bool(guards) and all(x['exactNoRegression'] for x in guards);all_pass=all_pass and no_reg and not errors and int(df.marketId.nunique())>=8;status='TESTED_KEEP_SIGNAL' if all_pass else 'TESTED_REJECTED';df.to_csv(ROWS,index=False)
 art={'version':'R4_MANAGEMENT_PROGRESS_HFT_REPLICATION5_V1','researchOnly':True,'actionAuthority':False,'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'cohort':{'requested':len(ids),'loaded':len(guards),'eligibleRows':int(len(df)),'marketsWithRows':int(df.marketId.nunique())},'metrics':metrics,'executionNoRegression':{'marketsChecked':len(guards),'allExact':no_reg,'failures':[x for x in guards if not x['exactNoRegression']]},'status':status,'fixedRulePassed':all_pass,'errors':errors,'rowsArtifact':str(ROWS.relative_to(ROOT)).replace('\\','/'),'guards':['60-180s only, matching M0 authority phase.','Progress facts use exact OUR logical owner/fill state.','No future-submit label used.','Future HFT path outcomes are scoring only.','No HFT refit or threshold tuning.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(art,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
