from __future__ import annotations
import argparse,json,lzma,sys,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
from sklearn.ensemble import HistGradientBoostingClassifier
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as sh
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_progress_hft_replication7_96_preregistered.json'
STACK=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'
TARGET=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s'];FULL=BASE+PROG
OUTCOMES=['futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)
def load(mid):
 p=SRC/f'{int(mid)}_r2_hft_closed_loop_v1.json.xz'
 if not p.exists():return None
 try:
  with lzma.open(p,'rt',encoding='utf-8') as fh:d=json.load(fh)
 except Exception:return None
 if 'R2_RESIDUAL' not in str(d.get('student') or '') or not (d.get('orderMeta') or {}):return None
 if not (ROOT/'data/execution_tape_v1/markets'/f'{int(mid)}.json.xz').exists():return None
 return d
def sig(r):return {k:r.get(k) for k in ['makerFilledShares','earlyMakerFillShares','earlySurplusFillShares','earlyFloorDamage','agedOptionFillShares','everSafe','durableBase','firstSafeMs','firstDurableMs','final','positiveDurationSec','maxFloorDrawdown','minFloor','counts','cancelReasons']}
def same(a,b):return json.dumps(a,sort_keys=True,allow_nan=True)==json.dumps(b,sort_keys=True,allow_nan=True)
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 if len(np.unique(y))<2:return {'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--batch',type=int,required=True);a=ap.parse_args();assert 1<=a.batch<=4
 pre=json.loads(PREREG.read_text(encoding='utf-8'));allids=list(map(int,pre['marketIds']));ids=allids[(a.batch-1)*24:a.batch*24]
 stack=joblib.load(STACK);td=pd.read_csv(TARGET).replace([np.inf,-np.inf],np.nan).dropna(subset=FULL).copy();td=td[(td.seconds_left>=60)&(td.seconds_left<=300)].sort_values(['market_id','t']);ms=td.groupby('market_id').t.min().sort_values().index.astype(int).tolist();tr=td[(td.market_id.isin(ms[:240]))&(td.build_now==1)].copy();m0b=hgb(26082801).fit(tr[BASE],tr.continue_weak_5s.astype(int));m0=stack['M0_model']
 rows=[];guards=[];errors=[]
 for mid in ids:
  d=load(mid)
  if d is None:errors.append({'marketId':mid,'error':'HFT_SOURCE_NOT_FOUND'});continue
  try:
   plain=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);shadow=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True);eq=same(sig(plain),sig(shadow));guards.append({'marketId':mid,'exactNoRegression':eq});rows.extend(shadow.get('managementShadowRows',[]));print(json.dumps({'market':mid,'rows':len(shadow.get('managementShadowRows',[])),'noRegression':eq}),flush=True)
  except Exception as e:errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'})
 df=pd.DataFrame(rows).replace([np.inf,-np.inf],np.nan)
 if df.empty:raise SystemExit('no management rows')
 df=df[(df.seconds_left>=60)&(df.seconds_left<180)&(df.build_now==1)].dropna(subset=FULL).copy();df['p_base']=m0b.predict_proba(df[BASE])[:,1];df['p_progress']=m0.predict_proba(df[FULL])[:,1]
 metrics={}
 for lab in OUTCOMES:
  b=met(df[lab],df.p_base);p=met(df[lab],df.p_progress);metrics[lab]={'BASE':b,'PROGRESS':p}
 outj=ROOT/f'data/research/r4_v0/hourly/r4_management_progress_hft_replication7_96_b{a.batch}.json';outc=ROOT/f'data/research/r4_v0/hourly/r4_management_progress_hft_replication7_96_b{a.batch}_rows.csv';df.to_csv(outc,index=False)
 art={'version':'R4_MANAGEMENT_PROGRESS_HFT_REPLICATION7_96_BATCH','batch':a.batch,'marketIds':ids,'eligibleRows':int(len(df)),'marketsWithRows':int(df.marketId.nunique()),'metrics':metrics,'executionNoRegression':{'marketsChecked':len(guards),'allExact':bool(guards) and all(x['exactNoRegression'] for x in guards),'failures':[x for x in guards if not x['exactNoRegression']]},'errors':errors,'rowsArtifact':str(outc.relative_to(ROOT)).replace('\\','/')};outj.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(outj.relative_to(ROOT)),'eligibleRows':len(df),'marketsWithRows':df.marketId.nunique(),'noReg':art['executionNoRegression']['allExact'],'errors':errors},ensure_ascii=False))
if __name__=='__main__':main()
