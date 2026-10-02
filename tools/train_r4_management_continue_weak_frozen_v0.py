from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_router_v0_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_continue_weak_frozen_v0.json'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_management_continue_weak_frozen_v0.joblib'
PORT=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit']
SEM=['placement_readiness_native_5s']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
RAW=['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
ROUTED=PORT+SEM+RESP+MEM
RAWFULL=PORT+SEM+RAW+RESP+MEM

def model(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=260,random_state=seed)
def mt(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);d.market_id=d.market_id.astype(int)
 # M0 only: existing BUILD responsibility, Formation management phase 60-300s.
 d=d[(d.build_now.astype(int)==1)&(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=RAWFULL+['continue_weak_5s','t']).copy().sort_values(['market_id','t'])
 ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=max(12,int(len(ms)*.55));rem=len(ms)-initial;sizes=[rem//3]*3
 for i in range(rem%3):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'rows':int(len(te)),'models':{}}
  for j,(name,feats) in enumerate([('ROUTED_MANAGER',ROUTED),('RAW_CONTEXT_MANAGER',RAWFULL)]):
   m=model(20269000+bi*10+j);m.fit(tr[feats],tr.continue_weak_5s);p=m.predict_proba(te[feats])[:,1];b['models'][name]=mt(te.continue_weak_5s,p)
  blocks.append(b)
 def s(name):
  q=[b['models'][name] for b in blocks];return {'meanAuc':float(np.mean([x['auc'] for x in q])),'worstAuc':float(np.min([x['auc'] for x in q])),'stdAuc':float(np.std([x['auc'] for x in q])),'meanAp':float(np.mean([x['ap'] for x in q])),'worstAp':float(np.min([x['ap'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'worstLogLoss':float(np.max([x['logLoss'] for x in q]))}
 summary={n:s(n) for n in ['ROUTED_MANAGER','RAW_CONTEXT_MANAGER']}
 cut=max(1,int(len(ms)*.8));trainm=ms[:cut];holdm=ms[cut:];tr=d[d.market_id.isin(trainm)];ho=d[d.market_id.isin(holdm)]
 final=model(20269991);final.fit(tr[ROUTED],tr.continue_weak_5s);hp=final.predict_proba(ho[ROUTED])[:,1];hold=mt(ho.continue_weak_5s,hp)
 joblib.dump({'version':'R4_MANAGEMENT_CONTINUE_WEAK_FROZEN_V0','model':final,'features':ROUTED,'authority':'RESEARCH_ONLY','phase':'60-300s','label':'continue_weak_5s'},MODEL)
 art={'version':'R4_MANAGEMENT_CONTINUE_WEAK_FROZEN_V0','researchOnly':True,'actionAuthority':False,'curriculum':'M0_RESPONSIBILITY_CONTINUATION','coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'positiveRate':float(d.continue_weak_5s.mean()),'phase':'60-300s'},'architecture':{'ROUTED_MANAGER':ROUTED,'RAW_CONTEXT_MANAGER_DIAGNOSTIC':RAWFULL,'note':'Routed manager receives semantic placement readiness plus portfolio/responsibility/lifecycle state; raw Predict/strike context is diagnostic only.'},'blocks':blocks,'summary':summary,'frozenResearchModel':{'path':str(MODEL.relative_to(ROOT)).replace('\\','/'),'trainMarkets':len(trainm),'holdoutMarkets':len(holdm),'holdout':hold},'guards':['No order/price/size/channel target.','Manager predicts continuation of an already-existing weak responsibility only.','0-60s excluded from M0; late protection management is separate.','R3.1 remains information-only; runtime analogue for responsibility/memory features is OUR owner ledger + R3.1 confirmed lifecycle facts.','No winner/settlement/future feature; future continuation is label only.','No threshold sweep or action authority.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':summary,'frozenResearchModel':art['frozenResearchModel']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
