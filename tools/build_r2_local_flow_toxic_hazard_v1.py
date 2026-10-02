from __future__ import annotations
import json,zlib,sqlite3,math
from pathlib import Path
import numpy as np,pandas as pd,joblib
from bisect import bisect_left,bisect_right
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'; DB=ROOT/'data/wallet_maker_book_inference.db'
SRC=D/'r2_joint_toxic_fill_hazard_v0_dataset.csv'; OUT=D/'r2_local_flow_toxic_hazard_v1_dataset.csv'; ART=D/'r2_local_flow_toxic_hazard_v1.joblib'; REP=D/'r2_local_flow_toxic_hazard_v1_report.json'
BASE=['side_is_up','order_age_ms','quote_price','status_none','status_new','status_partial','cum_exec_qty','remaining_qty','remaining_ratio','partial_fill_ratio','active_same_count','active_opp_count','quote_offset_ticks','current_bid','current_ask','current_spread_ticks','initial_depth','public_cum_depletion','public_depletion_ratio','public_any_depletion','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s']
FLOW=['same_px_add_1s','same_px_remove_1s','same_px_add_3s','same_px_remove_3s','native_side_add_1s','native_side_remove_1s','native_side_add_3s','native_side_remove_3s','opp_book_add_1s','opp_book_remove_1s','opp_book_add_3s','opp_book_remove_3s','same_px_net_1s','same_px_net_3s','native_flow_imb_1s','native_flow_imb_3s']
FEATURES=BASE+FLOW

def dec(b): return json.loads(zlib.decompress(b).decode('utf-8')) if b else {}
def metrics(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def load_changes(mid:int):
 con=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True); con.row_factory=sqlite3.Row
 try: rows=con.execute('select source_timestamp_ms,changes_z,is_checkpoint from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)).fetchall()
 finally: con.close()
 out=[]
 for r in rows:
  if int(r['is_checkpoint']): continue
  ch=dec(r['changes_z']); ev=[]
  for side in ('bids','asks'):
   for z in ch.get(side,[]) or []:
    try: ev.append((side,float(z.get('price')),float(z.get('delta',0.0))))
    except Exception: pass
  if ev: out.append((int(r['source_timestamp_ms']),ev))
 return out

def window(evs,times,cp,win,native_side,native_px):
 lo=bisect_right(times,cp-win); hi=bisect_left(times,cp)
 same_add=same_rem=ns_add=ns_rem=op_add=op_rem=0.0
 opp='asks' if native_side=='bids' else 'bids'
 for _,changes in evs[lo:hi]:
  for side,px,de in changes:
   q=abs(de)
   if side==native_side:
    if de>0: ns_add+=q
    elif de<0: ns_rem+=q
    if abs(px-native_px)<=1e-9:
     if de>0:same_add+=q
     elif de<0:same_rem+=q
   elif side==opp:
    if de>0:op_add+=q
    elif de<0:op_rem+=q
 den=ns_add+ns_rem
 return same_add,same_rem,ns_add,ns_rem,op_add,op_rem,(ns_add-ns_rem)/den if den>1e-12 else 0.0

def main():
 d=pd.read_csv(SRC,low_memory=False); pieces=[]
 for i,(mid,g) in enumerate(d.groupby('market_id'),1):
  mid=int(mid); evs=load_changes(mid); times=[x[0] for x in evs]; rows=[]
  for _,r in g.iterrows():
   cp=int(r.checkpoint_ms); up=float(r.side_is_up)>=0.5; q=float(r.quote_price); native_side='bids' if up else 'asks'; native_px=q if up else 1.0-q
   w1=window(evs,times,cp,1000,native_side,native_px); w3=window(evs,times,cp,3000,native_side,native_px)
   z=r.to_dict();
   z.update({'same_px_add_1s':w1[0],'same_px_remove_1s':w1[1],'same_px_add_3s':w3[0],'same_px_remove_3s':w3[1],'native_side_add_1s':w1[2],'native_side_remove_1s':w1[3],'native_side_add_3s':w3[2],'native_side_remove_3s':w3[3],'opp_book_add_1s':w1[4],'opp_book_remove_1s':w1[5],'opp_book_add_3s':w3[4],'opp_book_remove_3s':w3[5],'same_px_net_1s':w1[0]-w1[1],'same_px_net_3s':w3[0]-w3[1],'native_flow_imb_1s':w1[6],'native_flow_imb_3s':w3[6]}); rows.append(z)
  pieces.append(pd.DataFrame(rows));
  if i%20==0: print(json.dumps({'markets':i,'rows':sum(len(x) for x in pieces)}),flush=True)
 x=pd.concat(pieces,ignore_index=True); x.to_csv(OUT,index=False)
 mids=sorted(x.groupby('market_id').checkpoint_ms.max().to_dict(),key=lambda m:x[x.market_id==m].checkpoint_ms.max());a=int(len(mids)*.70);b=int(len(mids)*.85);sets={'train':set(mids[:a]),'validation':set(mids[a:b]),'test':set(mids[b:])};tr=x[x.market_id.isin(sets['train'])]
 def fit(feats):
  m=Pipeline([('imp',SimpleImputer(strategy='median')),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.045,max_iter=180,l2_regularization=3.0,min_samples_leaf=20,random_state=20260822))]);m.fit(tr[feats],tr.label_toxic_fill_1s.astype(int));return m
 base=fit(BASE); aug=fit(FEATURES); mets={}
 for k,s in sets.items():
  q=x[x.market_id.isin(s)]; mets[k]={'base':metrics(q.label_toxic_fill_1s.astype(int),base.predict_proba(q[BASE])[:,1]),'localFlow':metrics(q.label_toxic_fill_1s.astype(int),aug.predict_proba(q[FEATURES])[:,1])}
 joblib.dump({'version':'R2_LOCAL_FLOW_TOXIC_HAZARD_V1','model':aug,'features':FEATURES,'baseFeatures':BASE,'flowFeatures':FLOW,'trainingMarkets':sorted(sets['train']),'runtimeTargetAllowed':False,'semantics':'P(next1s HFT fill with negative +1s markout | strict-past working-order + Predict local order flow)'},ART)
 rep={'version':'R2_LOCAL_FLOW_TOXIC_HAZARD_V1','researchOnly':True,'rows':len(x),'markets':len(mids),'splitMarkets':{k:len(v) for k,v in sets.items()},'metrics':mets,'guardrails':['Predict public order-flow only','No Target/winner/PnL runtime input','Flow windows strictly before checkpoint','Chronological split','Same model family/hyperparameters for base vs augmented; no threshold sweep']};REP.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
