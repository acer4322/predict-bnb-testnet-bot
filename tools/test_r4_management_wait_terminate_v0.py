from __future__ import annotations
import json, sqlite3
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_router_v0_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_wait_terminate_v0.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_wait_terminate_v0_rows.csv'
PORT=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','placement_readiness_native_5s']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
ROUTED=PORT+RESP+MEM
RAW=ROUTED+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=18,l2_regularization=1,max_iter=220,random_state=seed)
def qdf(db,sql,params=()):
 c=sqlite3.connect(f'file:{db}?mode=ro',uri=True);c.execute('pragma query_only=on');d=pd.read_sql_query(sql,c,params=params);c.close();return d
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))}
def summ(bs,key):
 x=[b[key] for b in bs if b.get(key)];return {'blocks':len(x),'meanAuc':float(np.mean([z['auc'] for z in x])),'worstAuc':float(np.min([z['auc'] for z in x])),'stdAuc':float(np.std([z['auc'] for z in x])),'meanAp':float(np.mean([z['ap'] for z in x])),'worstAp':float(np.min([z['ap'] for z in x])),'meanLogLoss':float(np.mean([z['logLoss'] for z in x])),'worstLogLoss':float(np.max([z['logLoss'] for z in x]))}
def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);d.market_id=d.market_id.astype(int)
 # M1 only starts after an existing BUILD responsibility, when no new weak event appears in next 5s.
 d=d[(d.build_now==1)&(d.continue_weak_5s==0)&(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=RAW+['market_id','t','weak_side']).copy()
 mids=sorted(int(x) for x in d.market_id.unique());ph=','.join('?'*len(mids))
 life=qdf('data/wallet_maker_book_inference.db',f"select market_id,target_side,placement_first_ms,last_target_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",mids)
 groups={int(m):g for m,g in life.groupby('market_id')};wait=[];future_owners=[]
 for r in d.itertuples():
  g=groups.get(int(r.market_id));tf=int(r.t)+5000
  if g is None: wait.append(np.nan);future_owners.append(np.nan);continue
  a=g[(g.placement_first_ms<tf)&(g.last_target_ms>=tf)&(g.target_side.astype(str)==str(r.weak_side))]
  n=int(len(a));future_owners.append(n);wait.append(int(n>0))
 d['weak_owner_alive_5s']=future_owners;d['wait_5s']=wait;d=d.dropna(subset=['wait_5s']).copy();d['terminate_5s']=(1-d.wait_5s.astype(int)).astype(int);d.to_csv(ROWS,index=False)
 ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=max(14,int(len(ms)*.55));rem=len(ms)-initial;sizes=[rem//3]*3
 for i in range(rem%3):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'rows':int(len(te)),'waitRate':float(te.wait_5s.mean()) if len(te) else None}
  if len(te)>0 and tr.wait_5s.nunique()>1 and te.wait_5s.nunique()>1:
   for j,(name,feats) in enumerate([('PORTFOLIO_ONLY',PORT),('PLUS_RESP',PORT+RESP),('PLUS_RESP_MEMORY',ROUTED),('RAW_CONTEXT_DIAGNOSTIC',RAW)]):
    m=hgb(7000+bi*20+j).fit(tr[feats],tr.wait_5s.astype(int));p=m.predict_proba(te[feats])[:,1];b[name]=met(te.wait_5s,p)
  blocks.append(b)
 summary={k:summ(blocks,k) for k in ['PORTFOLIO_ONLY','PLUS_RESP','PLUS_RESP_MEMORY','RAW_CONTEXT_DIAGNOSTIC'] if any(k in b for b in blocks)}
 if 'PORTFOLIO_ONLY' in summary and 'PLUS_RESP_MEMORY' in summary:
  a=summary['PORTFOLIO_ONLY'];q=summary['PLUS_RESP_MEMORY'];summary['MANAGEMENT_DELTA']={'meanAuc':q['meanAuc']-a['meanAuc'],'worstAuc':q['worstAuc']-a['worstAuc'],'meanAp':q['meanAp']-a['meanAp'],'logLossImprovement':a['meanLogLoss']-q['meanLogLoss']}
 art={'version':'R4_MANAGEMENT_WAIT_TERMINATE_V0','researchOnly':True,'actionAuthority':False,'curriculum':'M1_WAIT_VS_TERMINATE_PILOT','question':'Conditional on an existing BUILD responsibility and no new weak responsibility event within 5s, does a weak-side resting responsibility remain alive at +5s (WAIT) or disappear (TERMINATE)?','coverage':{'markets':int(d.market_id.nunique()),'rows':int(len(d)),'waitRate':float(d.wait_5s.mean()) if len(d) else None,'phase':'60-300s','labelSource':'future inferred Target resting-owner lifecycle; label only'},'features':{'portfolio':PORT,'responsibility':RESP,'memory':MEM,'rawDiagnostic':RAW},'summary':summary,'blocks':blocks,'rowsArtifact':str(ROWS.relative_to(ROOT)).replace('\\','/'),'guards':['Future +5s owner state is teacher label only, never runtime input.','M1 is conditioned on current BUILD and no next weak event within 5s; it does not create a new responsibility.','R3.1 runtime analogue is unresolved child ownership/age/terminal certainty only; R3.1 retains no action authority.','No threshold sweep, no order/price/size/channel target, no winner/settlement.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':summary,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
