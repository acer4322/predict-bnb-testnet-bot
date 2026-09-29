from __future__ import annotations
import json,sqlite3,sys,math
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import compare_r3_vs_r4_management_exact_v7 as v7
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.compare_r3_vs_r4_management_control_v1 import mgmt_features,summary
from tools.compare_r3_vs_r4_formation_path_scale_v5 import path_features,scale_pf
from src.predict_bot import r3_dual_paper_shadow_v2 as r3
DB=ROOT/'data/strategy_target_compare_v1.db'; TAPE=ROOT/'data/execution_tape_v1/markets'; OUT=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_episode_v0.json'; ROWS=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_episode_v0_rows.csv'; MOD=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_episode_v0.joblib'
HOLDOUT={1737182,1737073,1737070,1737069,1737059,1736998,1736995,1736982,1736979,1736969,1736958,1736560}
# Use earlier realistic-HFT markets only for fitting; newest 12 remain untouched holdout.
def eligible_markets(limit=8):
 c=sqlite3.connect(DB);rows=c.execute("select market_id,max(decision_ms) t,count(*) n from our_decisions where strategy_version=? group by market_id having n>20 and t<? order by t desc",(v7.base.VERSION,1787813401971)).fetchall();c.close();out=[]
 for mid,t,n in rows:
  mid=int(mid)
  if mid in HOLDOUT:continue
  if (TAPE/f'{mid}.json.xz').exists():out.append(mid)
  if len(out)>=limit:break
 return list(reversed(out))
def auc(y,p):
 return float(roc_auc_score(y,p)) if len(set(map(int,y)))>1 else None

def main():
 mids=eligible_markets(); allrows=[]; market_summary=[]
 for j,mid in enumerate(mids):
  ev=[]
  try:
   A=summary(r3ctl.run_market(mid,True)); off=summary(v7.run_overlay(mid,False,[]))
   if A!=off: market_summary.append({'marketId':mid,'seedEquivalent':False});continue
   on=v7.run_overlay(mid,True,ev);B=summary(on);df=float(B['worstCaseFloor']-A['worstCaseFloor'])
   forced=[e for e in ev if bool(e.get('forcedCross')) and not e.get('error')]
   # Episode label is semantic, not threshold-mined: positive iff management episode improves final worst-case floor.
   y=int(df>0)
   for i,e in enumerate(forced):
    row={'marketId':mid,'episodeIndex':i,'atMs':int(e['atMs']),'y_value_cross':y,'deltaFloorEpisode':df,'risk':float(e.get('risk') or 0),'transition':float(e.get('transition') or 0),'pFormationPath':float(e.get('pFormationPath') or 0),'floor':float(e.get('floor') or 0),'surplus':float(e.get('surplus') or 0)}
    # scale-invariant local geometry available directly from logged quantities
    row['surplus_over_floor_scale']=row['surplus']/max(abs(row['floor'])+1.0,1.0)
    row['floor_sign']=float(row['floor']>0);row['floor_abs']=abs(row['floor'])
    allrows.append(row)
   market_summary.append({'marketId':mid,'seedEquivalent':True,'deltaFloor':df,'forcedCandidates':len(forced),'label':y})
   pass
  except Exception as ex:
   market_summary.append({'marketId':mid,'error':str(ex)})
 df=pd.DataFrame(allrows)
 if len(df)==0: raise RuntimeError('no rows')
 df.to_csv(ROWS,index=False)
 feats=['risk','transition','pFormationPath','floor','surplus','surplus_over_floor_scale','floor_abs']
 # chronology split by markets, newest 30% untouched validation
 vm=[m for m in mids if m in set(df.marketId.astype(int))];cut=max(1,int(len(vm)*.7));tr=set(vm[:cut]);va=set(vm[cut:]);X=df[feats].replace([np.inf,-np.inf],np.nan).fillna(0.);y=df.y_value_cross.astype(int);itr=df.marketId.isin(tr);iva=df.marketId.isin(va)
 model=HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=140,l2_regularization=4.,random_state=91,class_weight='balanced').fit(X[itr],y[itr])
 res={}
 for name,mask in [('train',itr),('val',iva)]:
  yy=y[mask];p=model.predict_proba(X[mask])[:,1];res[name]={'rows':int(mask.sum()),'markets':int(df.loc[mask,'marketId'].nunique()),'rate':float(yy.mean()),'auc':auc(yy,p),'ap':float(average_precision_score(yy,p)),'logLoss':float(log_loss(yy,p,labels=[0,1]))}
 joblib.dump({'version':'R4_CROSS_FOR_VALUE_EPISODE_V0','features':feats,'model':model,'actionAuthority':False,'label':'episode final floor delta > 0; future outcome offline only'},MOD)
 out={'version':'R4_CROSS_FOR_VALUE_EPISODE_V0','definition':'Research-only episode teacher on earlier exact-overlay realistic-HFT markets. Candidate rows are management forced-CROSS states not protected by Formation Path/positive-floor gates. Label=1 iff the full management episode improves final worst-case floor vs exact seed-equivalent R3. This is screening evidence only, not action-level causal authority. Latest 12 markets excluded from fitting.','marketsRequested':mids,'marketSummary':market_summary,'rows':len(df),'positiveRate':float(y.mean()),'features':feats,'split':{'trainMarkets':len(tr),'valMarkets':len(va)},'results':res,'artifacts':{'rows':str(ROWS.relative_to(ROOT)),'model':str(MOD.relative_to(ROOT))},'researchOnly':True,'actionAuthority':False}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()

