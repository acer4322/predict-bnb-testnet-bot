from __future__ import annotations
import json,lzma
from pathlib import Path
import numpy as np,pandas as pd
import sys
if str(ROOT:=Path(__file__).resolve().parents[1]) not in sys.path: sys.path.insert(0,str(ROOT))
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as sh
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
PREREG=ROOT/'data/research/r4_v0/hourly/r4_rolling_gap_owner_fresh24_preregistered.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
OUT=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_fresh24_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_fresh24_v1_rows.csv'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
PORT_CTX=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
PREP=PORT_CTX+['placement_readiness_native_5s']
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=260,random_state=seed)
def aucpack(y,p):
 y=np.asarray(y,dtype=int);p=np.asarray(p,float)
 if len(np.unique(y))<2:return {'n':int(len(y)),'rate':float(np.mean(y)) if len(y) else None}
 return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def load_hft(ids):
    out={}
    for mid in map(int,ids):
        p=SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz'
        if not p.exists():
            continue
        try:
            with lzma.open(p,'rt',encoding='utf-8') as fh:d=json.load(fh)
        except Exception:
            continue
        student=str(d.get('student') or '')
        if 'R2_RESIDUAL' not in student or not (d.get('orderMeta') or {}):
            continue
        if not (ROOT/'data/execution_tape_v1/markets'/f'{mid}.json.xz').exists():
            continue
        out[mid]=d
    return out

def main():
 # Earlier placement expert -> semantic readiness.
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p.market_id=p.market_id.astype(int);p['predict_edge']=(p.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p=p.dropna(subset=NATIVE+[lab,'decision_sampled_at_ms']).copy();p[lab]=p[lab].astype(int);ready=hgb(20265201);ready.fit(p[NATIVE],p[lab])
 # Later Target Formation cohort -> portable BUILD and PREPARE heads. Entire cohort is still strictly earlier than HFT shadow markets.
 f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f=f.dropna(subset=PORT_CTX+NATIVE+['first_event_ms','side','weak_side','is_add']).copy();f['placement_readiness_native_5s']=ready.predict_proba(f[NATIVE])[:,1];f=f.sort_values(['market_id','first_event_ms']).copy();g=f.groupby('market_id');f['next_ms']=g.first_event_ms.shift(-1);f['next_side']=g.side.shift(-1);f=f[f.next_ms.notna()].copy();f['dt']=f.next_ms-f.first_event_ms;f['prepare_weak5']=((f.dt>0)&(f.dt<=5000)&(f.next_side==f.weak_side)).astype(int);f['build']=(f.is_add.astype(int)==0).astype(int)
 build=hgb(20265301);build.fit(f[PORT_CTX],f.build);prep0=hgb(20265302);prep0.fit(f[PORT_CTX],f.prepare_weak5);prep1=hgb(20265303);prep1.fit(f[PREP],f.prepare_weak5)
 ids=json.loads(PREREG.read_text(encoding='utf-8'))['marketIds'];hd=load_hft(ids);market_rows=[];shadow_rows=[];errors=[]
 for mid in ids:
  d=hd.get(int(mid))
  if d is None:errors.append({'marketId':mid,'error':'HFT_SOURCE_NOT_FOUND'});continue
  try:
   r=sh.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True);market_rows.append({k:v for k,v in r.items() if k!='shadowRows'})
   for x in r.get('shadowRows',[]):x['durableBaseMarket']=1 if r.get('durableBase') else 0;x['finalFloorMarket']=float(r['final']['floor']);x['finalAbsNetMarket']=float(r['final']['absNet']);shadow_rows.append(x)
   print(json.dumps({'market':mid,'shadowRows':len(r.get('shadowRows',[])),'durable':r.get('durableBase')},ensure_ascii=False),flush=True)
  except Exception as e:errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'})
 df=pd.DataFrame(shadow_rows)
 if df.empty:raise SystemExit('no shadow rows')
 df['placement_readiness_native_5s']=ready.predict_proba(df[NATIVE])[:,1];df['p_build']=build.predict_proba(df[PORT_CTX])[:,1];df['p_prepare_ctx']=prep0.predict_proba(df[PORT_CTX])[:,1];df['p_prepare_role_routed']=prep1.predict_proba(df[PREP])[:,1]
 df['floorImproved5s']=(df.floorDelta5s>1e-9).astype(int);df['absNetReduced5s']=(df.absNetDelta5s<-1e-9).astype(int)
 labels=['futureFrozenWeakNeed5s','futureWeakMakerFill5s','floorImproved5s','absNetReduced5s'];metrics={}
 for labx in labels:
  metrics[labx]={'ctx':aucpack(df[labx],df.p_prepare_ctx),'roleRouted':aucpack(df[labx],df.p_prepare_role_routed)}
  if 'auc' in metrics[labx]['ctx'] and 'auc' in metrics[labx]['roleRouted']:
   metrics[labx]['deltaAuc']=metrics[labx]['roleRouted']['auc']-metrics[labx]['ctx']['auc'];metrics[labx]['deltaAp']=metrics[labx]['roleRouted']['ap']-metrics[labx]['ctx']['ap'];metrics[labx]['logLossImprovement']=metrics[labx]['ctx']['logLoss']-metrics[labx]['roleRouted']['logLoss']
 # Descriptive quintiles only; never used as thresholds.
 q=pd.qcut(df.p_prepare_role_routed.rank(method='first'),5,labels=['Q1','Q2','Q3','Q4','Q5']);quints=[]
 for qi in ['Q1','Q2','Q3','Q4','Q5']:
  z=df[q==qi];quints.append({'q':qi,'n':int(len(z)),'meanPrepare':float(z.p_prepare_role_routed.mean()),'frozenWeakNeed5sRate':float(z.futureFrozenWeakNeed5s.mean()),'weakMakerFill5sRate':float(z.futureWeakMakerFill5s.mean()),'floorImprove5sRate':float(z.floorImproved5s.mean()),'absNetReduce5sRate':float(z.absNetReduced5s.mean()),'meanFloorDelta5s':float(z.floorDelta5s.mean()),'p10FloorDelta5s':float(z.floorDelta5s.quantile(.1)),'meanAbsNetDelta5s':float(z.absNetDelta5s.mean())})
 # Per-market paired descriptive shadow quality; no action changed.
 market_diag=[]
 for mid,z in df.groupby('marketId'):
  market_diag.append({'marketId':int(mid),'n':int(len(z)),'meanPrepare':float(z.p_prepare_role_routed.mean()),'p90Prepare':float(z.p_prepare_role_routed.quantile(.9)),'frozenWeakNeed5sRate':float(z.futureFrozenWeakNeed5s.mean()),'weakMakerFill5sRate':float(z.futureWeakMakerFill5s.mean()),'meanFloorDelta5s':float(z.floorDelta5s.mean()),'durableBase':bool(z.durableBaseMarket.iloc[0]),'finalFloor':float(z.finalFloorMarket.iloc[0]),'finalAbsNet':float(z.finalAbsNetMarket.iloc[0])})
 artifact={'version':'R4_PARALLEL_BELIEF_HFT_SHADOW_FRESH24_V1','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,'question':'Do Target-trained portable parallel beliefs retain useful semantics on OUR realistic HFT GAP_OWNER path without changing any action?','cohort':{'source':'r4_rolling_gap_owner_fresh24_preregistered.json','requestedMarkets':len(ids),'loadedMarkets':len(hd),'simulatedMarkets':len(market_rows),'shadowRows':int(len(df))},'modelTemporalGuard':{'placementMaxMs':int(p.decision_sampled_at_ms.max()),'formationMaxMs':int(f.first_event_ms.max()),'hftMarketsLaterById':True},'metrics':metrics,'prepareScoreQuintiles':quints,'marketDiagnostics':market_diag,'baselineHftAggregate':sh.agg(market_rows),'errors':errors,'guards':['ROLL_KEEP_GAP_OWNER behavior unchanged.','Beliefs are shadow only and cannot create/cancel/reprice orders.','Portable mapping: abs payoff gap=OUR absNet; risk deficit=max(0,-OUR floor).','No maker-only gap approximation.','Public features use receipt-aligned archived snapshots already used by HFT research.','Future frozen need/future HFT fill are scoring labels only, never model features.','Quintiles are descriptive only; no threshold tuning.']}
 OUT.write_text(json.dumps(artifact,ensure_ascii=False,indent=2),encoding='utf-8');df.to_csv(ROWS,index=False)
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohort':artifact['cohort'],'baseline':artifact['baselineHftAggregate'],'metrics':metrics,'quintiles':quints,'errors':errors[:5]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
