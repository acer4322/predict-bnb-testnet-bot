from __future__ import annotations
import json,lzma,math,sys,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as sh
SRC=ROOT/'data/hft_forward_paper_v1/markets'
PREREG=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_shadow_replication3_preregistered.json'
TARGET_CACHE=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
STACK=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v2.joblib'
PRIOR=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_replication3_v1.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_replication3_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_replication3_v1_rows.csv'
PORT=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
FULL=PORT+RESP+MEM
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']

def hgb(seed,cw=None):
 return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=35,l2_regularization=1,max_iter=220,random_state=seed,class_weight=cw)
def sqrtw(y):
 y=np.asarray(y,int);n=len(y);w={c:math.sqrt(n/(2*max(1,int((y==c).sum())))) for c in [0,1]};z=np.mean([w[int(v)] for v in y]);return {k:v/z for k,v in w.items()}
def binmet(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 if len(y)==0:return {'n':0}
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))}
def multimet(y,p):
 y=np.asarray(y);pred=np.asarray(CLASSES)[np.argmax(p,axis=1)];Y=label_binarize(y,classes=CLASSES)
 aps=[average_precision_score(Y[:,j],p[:,j]) if Y[:,j].sum()>0 else np.nan for j in range(3)]
 auc=float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro')) if len(set(y))==3 else None
 rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0)
 return {'n':int(len(y)),'classRates':{c:float(np.mean(y==c)) for c in CLASSES},'macroAuc':auc,'macroAp':float(np.nanmean(aps)),'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)},'predRates':{c:float(np.mean(pred==c)) for c in CLASSES}}
def load_hft(ids):
 out={}
 for mid in map(int,ids):
  p=SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz'
  if not p.exists():continue
  try:
   with lzma.open(p,'rt',encoding='utf-8') as fh:d=json.load(fh)
  except Exception:continue
  if 'R2_RESIDUAL' not in str(d.get('student') or '') or not (d.get('orderMeta') or {}):continue
  if not (ROOT/'data/execution_tape_v1/markets'/f'{mid}.json.xz').exists():continue
  out[mid]=d
 return out
def train_port_comparators():
 d=pd.read_csv(TARGET_CACHE).replace([np.inf,-np.inf],np.nan);d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=FULL).copy();d.market_id=d.market_id.astype(int);d=d.sort_values(['market_id','t'])
 ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();cut=min(240,len(ms)-40);tr=d[d.market_id.isin(ms[:cut])]
 tr0=tr[tr.build_now==1];m0=hgb(31001).fit(tr0[PORT],tr0.continue_weak_5s.astype(int))
 tr1=tr[(tr.build_now==1)&(tr.management_label_5s.notna())].copy();tr1['event5']=(tr1.management_label_5s!='OBSERVE_NO_EVENT').astype(int);me=hgb(31002).fit(tr1[PORT],tr1.event5)
 rr=tr1[tr1.event5==1].copy();rr['route_continue']=(rr.management_label_5s=='CONTINUE_WEAK').astype(int);mr=hgb(31003,sqrtw(rr.route_continue)).fit(rr[PORT],rr.route_continue)
 return m0,me,mr

def aggregate_equal(now,prior):
 keys=['markets','makerFilledShares','earlyMakerFillShares','earlyFillRate','earlySurplusFillShares','earlyFloorDamage','agedOptionFillShares','everSafeMarkets','durableBaseMarkets','durableBaseRate','medianFinalFloor','p10FinalFloor','medianFinalAbsNet','p90FinalAbsNet']
 dif={}
 for k in keys:
  a=now.get(k);b=prior.get(k)
  if isinstance(a,(int,float)) and isinstance(b,(int,float)):dif[k]=float(a)-float(b)
  else:dif[k]=None if a==b else {'now':a,'prior':b}
 return {'exactNumeric':all(v is not None and abs(v)<=1e-9 for v in dif.values() if isinstance(v,(int,float))),'diff':dif}

def main():
 ids=json.loads(PREREG.read_text(encoding='utf-8'))['marketIds'];hd=load_hft(ids);market_rows=[];rows=[];errors=[]
 for mid in ids:
  d=hd.get(int(mid))
  if d is None:errors.append({'marketId':mid,'error':'HFT_SOURCE_NOT_FOUND'});continue
  try:
   r=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True);market_rows.append({k:v for k,v in r.items() if k not in {'shadowRows','managementShadowRows'}});rows.extend(r.get('managementShadowRows',[]));print(json.dumps({'market':mid,'managementRows':len(r.get('managementShadowRows',[]))},ensure_ascii=False),flush=True)
  except Exception as e:errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'})
 df=pd.DataFrame(rows)
 if df.empty:raise SystemExit('no management shadow rows')
 df=df[(df.seconds_left>=60)&(df.seconds_left<=300)].replace([np.inf,-np.inf],np.nan).dropna(subset=FULL).copy();build=df[df.build_now==1].copy()
 stack=joblib.load(STACK);m0p,mep,mrp=train_port_comparators()
 # M0: Target-trained portfolio-only vs responsibility+memory full stack.
 build['p_m0_port']=m0p.predict_proba(build[PORT])[:,1];build['p_m0_full']=stack['M0_continue_weak_model'].predict_proba(build[FULL])[:,1]
 m0={'PORT':binmet(build.continue_weak_5s,build.p_m0_port),'FULL':binmet(build.continue_weak_5s,build.p_m0_full)}
 if m0['PORT'].get('auc') is not None and m0['FULL'].get('auc') is not None:m0['deltaFullVsPort']={'auc':m0['FULL']['auc']-m0['PORT']['auc'],'ap':m0['FULL']['ap']-m0['PORT']['ap'],'logLossImprovement':m0['PORT']['logLoss']-m0['FULL']['logLoss']}
 pathq={}
 for lab in ['futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']:
  a=binmet(build[lab],build.p_m0_port);b=binmet(build[lab],build.p_m0_full);q={'PORT':a,'FULL':b}
  if a.get('auc') is not None and b.get('auc') is not None:q['deltaFullVsPort']={'auc':b['auc']-a['auc'],'ap':b['ap']-a['ap'],'logLossImprovement':a['logLoss']-b['logLoss']}
  pathq[lab]=q
 # M1: same hierarchical architecture for portfolio comparator and full frozen V2.
 pe=mep.predict_proba(build[PORT])[:,1];pc=mrp.predict_proba(build[PORT])[:,1];pp=np.column_stack([pe*pc,pe*(1-pc),1-pe])
 fe=stack['M1_event_model'].predict_proba(build[FULL])[:,1];fc=stack['M1_route_continue_model'].predict_proba(build[FULL])[:,1];fp=np.column_stack([fe*fc,fe*(1-fc),1-fe])
 m1={'PORT':multimet(build.management_label_5s,pp),'FULL':multimet(build.management_label_5s,fp)}
 if m1['PORT'].get('macroAuc') is not None and m1['FULL'].get('macroAuc') is not None:m1['deltaFullVsPort']={'macroAuc':m1['FULL']['macroAuc']-m1['PORT']['macroAuc'],'macroAp':m1['FULL']['macroAp']-m1['PORT']['macroAp'],'logLossImprovement':m1['PORT']['logLoss']-m1['FULL']['logLoss'],'balancedAccuracy':m1['FULL']['balancedAccuracy']-m1['PORT']['balancedAccuracy'],'handoffRecall':m1['FULL']['perClassRecall']['HANDOFF_ALLOW']-m1['PORT']['perClassRecall']['HANDOFF_ALLOW'],'observeRecall':m1['FULL']['perClassRecall']['OBSERVE_NO_EVENT']-m1['PORT']['perClassRecall']['OBSERVE_NO_EVENT']}
 # Per-kind diagnostic only, no threshold selection.
 kinddiag=[]
 for k,z in build.groupby('kind'):
  if len(z)<10:continue
  q={'kind':str(k),'n':int(len(z)),'continueRate':float(z.continue_weak_5s.mean()),'m0Full':binmet(z.continue_weak_5s,z.p_m0_full)};kinddiag.append(q)
 current=sh.agg(market_rows);prior=json.loads(PRIOR.read_text(encoding='utf-8'))['baselineHftAggregate'];guard=aggregate_equal(current,prior)
 # write rows with model scores for future cross-cohort replication.
 score=pd.DataFrame(build);score['p_event_full']=fe;score['p_route_continue_full']=fc;score['p_continue_full']=fp[:,0];score['p_handoff_full']=fp[:,1];score['p_observe_full']=fp[:,2];score.to_csv(ROWS,index=False)
 art={'version':'R4_MANAGEMENT_HFT_SHADOW_REPLICATION3_V1','researchOnly':True,'actionAuthority':False,'actionChanges':False,'question':'Does Target-trained responsibility-aware Management Stack V2 preserve its management-event semantics on OUR realistic HFT GAP_OWNER event stream, compared with an otherwise identical Target-trained portfolio-only manager?','cohort':{'marketsRequested':len(ids),'marketsLoaded':len(hd),'managementRows60to300':int(len(df)),'currentBuildRows':int(len(build)),'buildRate':float(df.build_now.mean())},'semanticMapping':{'eventAlignment':'state captured immediately BEFORE each OUR HFT owner submission; current submission excluded from existing-owner counts','buildNow':'current owner submission side is realized weak side before submission','responsibility':'exact OUR live HFT owners with submittedAt strictly before current event; cancel-requested remains owned until terminal','memory':'prior owner-submit event cadence and mode-run history, matching Target parent-event teacher semantics','portfolio':'abs_gap=absNet; risk_deficit=max(0,-floor); coverage=2*paired/gross; absnet_ratio=absNet/gross; floor_per_gross=floor/gross'},'M0_CONTINUE_WEAK':m0,'M0_PATH_QUALITY':pathq,'M1_HANDOFF_OBSERVE':m1,'perKind':kinddiag,'executionNoRegression':guard,'baselineHftAggregate':current,'errors':errors,'rowsArtifact':str(ROWS.relative_to(ROOT)).replace('\\','/'),'guards':['No action/cancel/reprice/size change.','Same ROLL_KEEP_GAP_OWNER simulator and cohort as prior fresh24 baseline.','Target future events are not HFT inputs; OUR future owner events are scoring labels only.','No threshold sweep.','Manager scores only current BUILD events in 60-300s, matching training curriculum.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohort':art['cohort'],'M0':m0,'M0PathQuality':pathq,'M1':m1,'noRegression':guard,'errors':errors[:5]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
