from __future__ import annotations
import json,lzma,sys,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as sh
from tools import test_r4_management_hft_shadow_fresh24_v1 as base
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_m0_phase_routing_replication4_preregistered.json'
STACK=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v2.joblib'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_m0_phase_routing_replication4_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_m0_phase_routing_replication4_v1_rows.csv'
PORT=base.PORT; FULL=base.FULL
OUTCOMES=['futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']

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
 pre=json.loads(PREREG.read_text(encoding='utf-8'));ids=list(map(int,pre['marketIds']));stack=joblib.load(STACK);m0p,_,_=base.train_port_comparators();rows=[];market_rows=[];guards=[];errors=[]
 for mid in ids:
  d=load(mid)
  if d is None:errors.append({'marketId':mid,'error':'HFT_SOURCE_NOT_FOUND'});continue
  try:
   plain=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);shadow=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True)
   eq=same(result_signature(plain),result_signature(shadow));guards.append({'marketId':mid,'exactNoRegression':eq})
   market_rows.append({k:v for k,v in shadow.items() if k not in {'shadowRows','managementShadowRows'}});rows.extend(shadow.get('managementShadowRows',[]));print(json.dumps({'market':mid,'managementRows':len(shadow.get('managementShadowRows',[])),'noRegression':eq}),flush=True)
  except Exception as e:errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'})
 df=pd.DataFrame(rows)
 if df.empty:raise SystemExit('no management rows')
 df=df[(df.seconds_left>=60)&(df.seconds_left<=300)&(df.build_now==1)].replace([np.inf,-np.inf],np.nan).dropna(subset=FULL).copy()
 df['p_port']=m0p.predict_proba(df[PORT])[:,1];df['p_full']=stack['M0_continue_weak_model'].predict_proba(df[FULL])[:,1];df['p_phase_routed']=np.where((df.seconds_left>=60)&(df.seconds_left<180),df.p_full,df.p_port)
 metrics={};all_pass=True
 for lab in OUTCOMES:
  a=met(df[lab],df.p_port);f=met(df[lab],df.p_full);r=met(df[lab],df.p_phase_routed);delta={'auc':r['auc']-a['auc'],'ap':r['ap']-a['ap'],'logLossImprovement':a['logLoss']-r['logLoss']};ok=all(v>=-1e-12 for v in delta.values());all_pass=all_pass and ok;metrics[lab]={'PORT':a,'FULL':f,'PHASE_ROUTED':r,'routedVsPort':delta,'passesFixedRule':ok}
 no_reg=bool(guards) and all(x['exactNoRegression'] for x in guards);all_pass=all_pass and no_reg and not errors
 status='TESTED_KEEP_SIGNAL' if all_pass else 'TESTED_REJECTED'
 df.to_csv(ROWS,index=False)
 art={'version':'R4_MANAGEMENT_M0_PHASE_ROUTING_REPLICATION4_V1','researchOnly':True,'actionAuthority':False,'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'cohort':{'requested':len(ids),'loaded':len(guards),'managementBuildRows60to300':int(len(df)),'marketsWithRows':int(df.marketId.nunique())},'routing':'60<=seconds_left<180 => frozen FULL M0; 180<=seconds_left<=300 => Target-trained portfolio-only M0','metrics':metrics,'executionNoRegression':{'marketsChecked':len(guards),'allExact':no_reg,'failures':[x for x in guards if not x['exactNoRegression']]},'status':status,'fixedRulePassed':all_pass,'errors':errors,'rowsArtifact':str(ROWS.relative_to(ROOT)).replace('\\','/'),'guards':['No model/threshold/phase change after preregistration.','No MAIN/OPTION future-submit label used for KEEP decision.','HFT path outcomes are scoring labels only.','Shadow collection cannot alter action state.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'cohort':art['cohort'],'metrics':metrics,'noRegression':art['executionNoRegression'],'errors':errors},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
