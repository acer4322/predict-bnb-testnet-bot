from __future__ import annotations
import bisect,json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv';FORM=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv';DB=ROOT/'data/public_research_archive_v1.db';OUT=ROOT/'data/research/r4_v0/hourly/r4_quote_commitment_with_readiness_v1.json'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
BASE=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant','weak_bid','weak_ask','weak_mid','weak_spread']
def mdl(seed):return HistGradientBoostingClassifier(learning_rate=.055,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=240,random_state=seed)
def met(y,p):return {'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'ll':float(log_loss(y,p,labels=[0,1]))}
def fit(tr,te,fs,seed):
 m=mdl(seed);m.fit(tr[fs],tr.y);return met(te.y,m.predict_proba(te[fs])[:,1])
def main():
 # frozen earlier native placement-readiness expert
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p['predict_edge']=(p.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p=p.dropna(subset=NATIVE+[lab]).copy();native=mdl(5101);native.fit(p[NATIVE],p[lab].astype(int));sourceMax=int(p.decision_sampled_at_ms.max())
 d=pd.read_csv(FORM).replace([np.inf,-np.inf],np.nan).sort_values(['first_event_ms','market_id']);d=d[d['mode'].astype(str).eq('REPAIR')].copy();d['predict_edge']=d.predict_edge.astype(float);mids=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index][:96];d=d[d.market_id.isin(mids)].copy();d['placement_readiness_native_5s']=native.predict_proba(d[NATIVE])[:,1]
 c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');pub={}
 for i in range(0,len(mids),80):
  blk=mids[i:i+80];q=','.join('?'*len(blk));rs=c.execute(f'''select market_id,sampled_at_ms,predict_up_bid,predict_up_ask,predict_up_mid,predict_down_bid,predict_down_ask,predict_down_mid from wallet_taker_signal_snapshots where market_id in ({q}) order by market_id,sampled_at_ms''',blk).fetchall()
  for r in rs:pub.setdefault(int(r['market_id']),[]).append((int(r['sampled_at_ms']),dict(r)))
 c.close();vs=[]
 for r in d.itertuples(index=False):
  arr=pub.get(int(r.market_id),[]);times=[x[0] for x in arr];j=bisect.bisect_left(times,int(r.first_event_ms))-1
  if j<0:vs.append(None);continue
  t,z=arr[j];lag=int(r.first_event_ms)-t
  if lag<0 or lag>1500:vs.append(None);continue
  pref='predict_up_' if str(r.weak_side)=='UP' else 'predict_down_';b,a,m=z[pref+'bid'],z[pref+'ask'],z[pref+'mid']
  if any(x is None for x in (b,a,m)):vs.append(None);continue
  vs.append((lag,float(b),float(a),float(m)))
 d['_p']=vs;d=d[d._p.notna()].copy();d['lag']=d._p.map(lambda x:x[0]);d['weak_bid']=d._p.map(lambda x:x[1]);d['weak_ask']=d._p.map(lambda x:x[2]);d['weak_mid']=d._p.map(lambda x:x[3]);d['weak_spread']=d.weak_ask-d.weak_bid;d.drop(columns=['_p'],inplace=True);d['y']=(d.price>=d.weak_bid-1e-9).astype(int)
 ordered=[int(x) for x in d.groupby('market_id').first_event_ms.min().sort_values().index];n=len(ordered);test=max(8,min(16,n//5));starts=sorted(set(s for s in [max(32,int(n*.45)),max(40,int(n*.60)),max(48,int(n*.75))] if s+test<=n));folds=[]
 for fi,s in enumerate(starts):
  tr=d[d.market_id.isin(ordered[:s])];te=d[d.market_id.isin(ordered[s:s+test])];b=fit(tr,te,BASE,5200+fi);x=fit(tr,te,BASE+['placement_readiness_native_5s'],5300+fi);folds.append({'fold':fi,'base':b,'plusReadiness':x,'increment':{'deltaAuc':x['auc']-b['auc'],'deltaAp':x['ap']-b['ap'],'llImprovement':b['ll']-x['ll']}})
 inc=[f['increment'] for f in folds];phase=[]
 for name,lo,hi in [('LATE_0_60',0,60),('MID_60_180',60,180),('EARLY_180_300',180,301)]:
  z=d[(d.seconds_left>=lo)&(d.seconds_left<hi)].copy();phase.append({'phase':name,'n':int(len(z)),'highCommitRate':float(z.y.mean()),'meanReadinessHighCommit':float(z.loc[z.y==1,'placement_readiness_native_5s'].mean()),'meanReadinessLowCommit':float(z.loc[z.y==0,'placement_readiness_native_5s'].mean())})
 art={'version':'R4_QUOTE_COMMITMENT_WITH_READINESS_V1','researchOnly':True,'actionAuthority':False,'layer':'EXECUTION_BELIEF_DIAGNOSTIC','coverage':{'markets':int(d.market_id.nunique()),'rows':len(d),'highCommitRate':float(d.y.mean())},'temporalGuard':{'sourcePlacementMaxMs':sourceMax,'quoteCohortMinMs':int(d.first_event_ms.min()),'sourceStrictlyEarlier':bool(sourceMax<d.first_event_ms.min())},'summary':{'meanDeltaAuc':float(np.mean([x['deltaAuc'] for x in inc])),'meanDeltaAp':float(np.mean([x['deltaAp'] for x in inc])),'meanLlImprovement':float(np.mean([x['llImprovement'] for x in inc])),'allAucPositive':all(x['deltaAuc']>0 for x in inc),'allApPositive':all(x['deltaAp']>0 for x in inc),'allLlPositive':all(x['llImprovement']>0 for x in inc)},'folds':folds,'phaseDescriptive':phase,'guards':['Readiness expert is trained only on earlier Target placement cohort and frozen before quote cohort.','Readiness is semantic placement participation belief, not raw external feed.','Quote posture label is at/above current public bid vs behind bid.','No action authority.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'temporalGuard':art['temporalGuard'],'summary':art['summary'],'phase':phase},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
