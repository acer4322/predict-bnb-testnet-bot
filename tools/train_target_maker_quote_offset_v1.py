from __future__ import annotations
import bisect,json,math,sqlite3
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import balanced_accuracy_score,f1_score,accuracy_score,roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
GENERAL=OUT/'target_general_maker_side_hazard_v1.csv'
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
DATA=OUT/'target_maker_quote_offset_v1_states.csv'; ART=OUT/'target_maker_quote_offset_v1.joblib'; REPORT=OUT/'target_maker_quote_offset_v1_report.json'
SEED=20260820; MAX_AGE_MS=1500; FINAL_HOLDOUT_START=1787137800000
CORE=['seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign']
BOOK=['up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth','pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge']
LIFE=['last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','maker_side_streak','taker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s']
ECON=['maker_up_avg_price','maker_down_avg_price','maker_avg_pair_edge','taker_up_avg_price','taker_down_avg_price','taker_avg_pair_edge','combined_up_avg_price','combined_down_avg_price','combined_avg_pair_edge']
PLACE=['last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms','placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s','up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak']
SIDE=['side_is_up','side_maker_net','side_combined_net','chosen_bid','chosen_ask','chosen_spread_ticks','opposite_bid_side','last_same_maker_age_ms','last_opp_maker_age_ms','same_placements_5s','opp_placements_5s','same_placements_10s','opp_placements_10s']
FEATURES=CORE+BOOK+LIFE+ECON+PLACE+SIDE
CLASSES=['AHEAD','AT_BID','ONE_BEHIND','TWO_THREE_BEHIND','FOUR_PLUS_BEHIND']

def cls(offset:int)->int:
 if offset<=-1:return 0
 if offset==0:return 1
 if offset==1:return 2
 if offset<=3:return 3
 return 4

def sc_features(r,side):
 up=side=='UP'; return {'side_is_up':float(up),'side_maker_net':float(r['maker_net'])*(1 if up else -1),'side_combined_net':float(r['combined_net'])*(1 if up else -1),'chosen_bid':float(r['up_bid'] if up else r['down_bid']),'chosen_ask':float(r['up_ask'] if up else r['down_ask']),'chosen_spread_ticks':float(r['up_spread_ticks'] if up else r['down_spread_ticks']),'opposite_bid_side':float(r['down_bid'] if up else r['up_bid']),'last_same_maker_age_ms':float(r['last_maker_up_age_ms'] if up else r['last_maker_down_age_ms']),'last_opp_maker_age_ms':float(r['last_maker_down_age_ms'] if up else r['last_maker_up_age_ms']),'same_placements_5s':float(r['up_placements_5s'] if up else r['down_placements_5s']),'opp_placements_5s':float(r['down_placements_5s'] if up else r['up_placements_5s']),'same_placements_10s':float(r['up_placements_10s'] if up else r['down_placements_10s']),'opp_placements_10s':float(r['down_placements_10s'] if up else r['up_placements_10s'])}

def build():
 g=pd.read_csv(GENERAL); g=g[g.market_end_ms.astype('int64')<FINAL_HOLDOUT_START].copy(); by={int(m):q.sort_values('checkpoint_ms').reset_index(drop=True) for m,q in g.groupby('market_id')}; c=sqlite3.connect(BOOK_DB); c.row_factory=sqlite3.Row; rows=[]; drops={'noGeneral':0,'noPrior':0,'stale':0,'badBid':0}
 for mid,q in by.items():
  ts=q.checkpoint_ms.astype('int64').to_numpy(); ps=list(c.execute('''select parent_id,target_side,target_price,placement_first_ms from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null order by placement_first_ms,parent_id''',(mid,)))
  for p in ps:
   t=int(p['placement_first_ms']); j=int(np.searchsorted(ts,t,side='left')-1)
   if j<0:drops['noPrior']+=1; continue
   age=t-int(ts[j]);
   if age<0 or age>MAX_AGE_MS:drops['stale']+=1;continue
   r=q.iloc[j]; side=str(p['target_side']); bid=float(r['up_bid'] if side=='UP' else r['down_bid']); price=float(p['target_price'])
   if not (math.isfinite(bid) and math.isfinite(price)):drops['badBid']+=1;continue
   off=int(round((bid-price)/.01)); z={k:r.get(k,math.nan) for k in CORE+BOOK+LIFE+ECON+PLACE}; z.update(sc_features(r,side)); z.update({'market_id':mid,'market_end_ms':int(r['market_end_ms']),'checkpoint_ms':int(r['checkpoint_ms']),'placement_ms':t,'parent_id':str(p['parent_id']),'side':side,'target_price':price,'offset_ticks':off,'offset_class':cls(off),'checkpoint_age_ms':age}); rows.append(z)
 c.close(); d=pd.DataFrame(rows).sort_values(['market_end_ms','placement_ms','market_id']).reset_index(drop=True); d.to_csv(DATA,index=False); return d,drops

def metric(y,p):
 pred=np.argmax(p,axis=1); y=np.asarray(y,int); out={'n':len(y),'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'macroF1':float(f1_score(y,pred,average='macro',zero_division=0)),'truthDistribution':{CLASSES[i]:int((y==i).sum()) for i in range(5)},'predDistribution':{CLASSES[i]:int((pred==i).sum()) for i in range(5)}}
 agg=(y<=1).astype(int); pagg=p[:,0]+p[:,1]; near=(y<=3).astype(int); pnear=p[:,:4].sum(axis=1)
 out['aggressiveAtOrAhead']={'rate':float(agg.mean()),'auc':float(roc_auc_score(agg,pagg)),'ap':float(average_precision_score(agg,pagg)),'logLoss':float(log_loss(agg,np.clip(pagg,1e-7,1-1e-7),labels=[0,1]))}
 out['within3TicksInclusive']={'rate':float(near.mean()),'auc':float(roc_auc_score(near,pnear)),'ap':float(average_precision_score(near,pnear)),'logLoss':float(log_loss(near,np.clip(pnear,1e-7,1-1e-7),labels=[0,1]))}
 # distance in representative ticks: AHEAD=-1, AT=0, ONE=1, MID=2.5, DEEP=4
 rep=np.array([-1.,0.,1.,2.5,4.]); dist=np.abs(rep[pred]-rep[y]); out['withinOneBucketRate']=float((np.abs(pred-y)<=1).mean()); out['meanRepresentativeTickError']=float(dist.mean()); return out

def main():
 if DATA.exists():
  d=pd.read_csv(DATA); drops={'reusedBuiltDataset':True}
 else:
  d,drops=build()
 mm=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']); ids=mm.market_id.astype(int).tolist(); n=len(ids); a=int(n*.70); b=int(n*.85); sp={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])}; parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in sp.items()}; m=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=96,max_interaction_bins=32,interactions=8,outer_bags=4,learning_rate=.035,max_rounds=1800,early_stopping_rounds=80,min_samples_leaf=12,n_jobs=-2,random_state=SEED); X=parts['train'][FEATURES].apply(pd.to_numeric,errors='coerce'); m.fit(X,parts['train'].offset_class.astype(int)); joblib.dump({'version':'TARGET_MAKER_QUOTE_OFFSET_V1','features':FEATURES,'classes':CLASSES,'model':m,'trainingMarkets':sorted(sp['train']),'trainingMaxEndMs':int(parts['train'].market_end_ms.max()),'finalClosedLoopHoldoutStartMs':FINAL_HOLDOUT_START},ART)
 metrics={};
 for k,x in parts.items():metrics[k]=metric(x.offset_class.astype(int),m.predict_proba(x[FEATURES].apply(pd.to_numeric,errors='coerce')))
 imp=list(m.term_importances()); names=list(m.term_names_); ix=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:20]; top=[{'term':str(names[i]),'importance':float(imp[i])} for i in ix]
 openq=d[d.seconds_left>240]; open_dist={CLASSES[i]:float((openq.offset_class==i).mean()) for i in range(5)}
 rep={'reportVersion':'TARGET_MAKER_QUOTE_OFFSET_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'goal':'Predict Target Maker quote aggressiveness/offset bucket from strict-past current coordination state at high-confidence inferred placement events.','dataset':{'rows':len(d),'markets':int(d.market_id.nunique()),'checkpointMaxAgeMs':MAX_AGE_MS,'drops':drops,'openRows':len(openq),'openDistribution':open_dist},'splitMarkets':{k:len(v) for k,v in sp.items()},'chronology':{'trainMaxEndMs':int(parts['train'].market_end_ms.max()),'validationMaxEndMs':int(parts['validation'].market_end_ms.max()),'testMaxEndMs':int(parts['test'].market_end_ms.max()),'reservedFinalClosedLoopStartMs':FINAL_HOLDOUT_START},'classes':CLASSES,'metrics':metrics,'topTerms':top,'artifact':str(ART),'references':{'oldDirectPublicOnlyAtOrAheadMeanAucApprox':0.6294,'oldDirectPublicOnlyNear2MeanAucApprox':0.6551},'guards':['Each placement is matched only to the latest checkpoint strictly before placement_first_ms; Target placement/price is label only.','No winner/PnL/future outcome features.','All source markets end before the reserved final start75 closed-loop holdout.','No hyperparameter sweep.','AHEAD bucket does not imply crossing the ask at runtime; execution policy must clamp to a passive price.']}; REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
