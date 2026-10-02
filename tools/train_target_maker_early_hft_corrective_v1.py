from __future__ import annotations
import bisect,json,math,sqlite3
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
TRAIN_FILES=[OUT/f'target_blind_promoted_controller_closed_loop_v2_hft_early_v1_{x}_states.csv' for x in ['fixed_train00_04','fixed_train05_09','fixed_train10_14','fixed_train15_19','fixed_train20_24','fixed_train25_29']]
VAL_FILES=[OUT/f'target_blind_promoted_controller_closed_loop_v2_hft_early_v1_{x}_states.csv' for x in ['fixed_teacherval30_34','fixed_teacherval35_39']]
UP_ART=OUT/'target_maker_early_hft_corrective_up_v1.joblib'; DN_ART=OUT/'target_maker_early_hft_corrective_down_v1.joblib'; REPORT=OUT/'target_maker_early_hft_corrective_v1_report.json'; DATA=OUT/'target_maker_early_hft_corrective_v1_states.csv'
SEED=20260820
CORE=['seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign']
BOOK=['up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth','pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge']
LIFE=['last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','maker_side_streak','taker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s']
ECON=['maker_up_avg_price','maker_down_avg_price','maker_avg_pair_edge','taker_up_avg_price','taker_down_avg_price','taker_avg_pair_edge','combined_up_avg_price','combined_down_avg_price','combined_avg_pair_edge']
PLACE=['last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms','placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s','up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak']
META=['pMakerUp','pMakerDown','pTaker1s','pTaker3s']
FEATURES=CORE+BOOK+LIFE+ECON+PLACE+META

def metrics(y,p):
 y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-7,1-1e-7); both=len(set(y.tolist()))>1
 return {'n':len(y),'positives':int(y.sum()),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p))}

def ebm(fs,seed):return ExplainableBoostingClassifier(feature_names=fs,max_bins=96,max_interaction_bins=32,interactions=8,outer_bags=4,learning_rate=.035,max_rounds=1800,early_stopping_rounds=80,min_samples_leaf=12,n_jobs=-2,random_state=seed)
def top(m,n=18):
 imp=list(m.term_importances()); names=list(m.term_names_); ix=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:n]; return [{'term':str(names[i]),'importance':float(imp[i])} for i in ix]

def load_teacher():
 c=sqlite3.connect(BOOK_DB); c.row_factory=sqlite3.Row; em={int(r['window_end_ms']):int(r['market_id']) for r in c.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null')}; times={}
 mids=set(em.values())
 for mid in mids:
  up=[];dn=[]
  for r in c.execute('''select target_side,placement_first_ms from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null order by placement_first_ms''',(mid,)):
   (up if str(r['target_side'])=='UP' else dn).append(int(r['placement_first_ms']))
  if up or dn:times[mid]=(up,dn)
 c.close(); return em,times

def label(df,split,em,times):
 out=[]
 for _,r in df.iterrows():
  we=int(r.windowEndMs); mid=em.get(we); z=times.get(mid)
  if z is None:continue
  cp=int(r.atMs); row=r.to_dict(); row['split']=split; row['targetMarketId']=mid
  for side,a in [('UP',z[0]),('DOWN',z[1])]:
   n=bisect.bisect_right(a,cp+1000)-bisect.bisect_right(a,cp); row[f'label_{side.lower()}_next1s']=int(n>0); row[f'target_{side.lower()}_count_next1s']=int(n)
  out.append(row)
 return out

def main():
 em,times=load_teacher(); rows=[]
 for p in TRAIN_FILES: rows+=label(pd.read_csv(p),'train',em,times)
 
 for p in VAL_FILES: rows+=label(pd.read_csv(p),'validation',em,times)
 d=pd.DataFrame(rows).sort_values(['windowEndMs','atMs','marketId']).reset_index(drop=True); d.to_csv(DATA,index=False)
 result={}
 for i,side in enumerate(('UP','DOWN')):
  lab=f'label_{side.lower()}_next1s'; basecol='pMakerUp' if side=='UP' else 'pMakerDown'; tr=d[d.split=='train'].copy(); va=d[d.split=='validation'].copy(); m=ebm(FEATURES,SEED+i); Xtr=tr[FEATURES].apply(pd.to_numeric,errors='coerce'); Xv=va[FEATURES].apply(pd.to_numeric,errors='coerce'); m.fit(Xtr,tr[lab].astype(int)); pp=m.predict_proba(Xv)[:,1]; art=UP_ART if side=='UP' else DN_ART; joblib.dump({'version':'TARGET_MAKER_EARLY_HFT_CORRECTIVE_V1','side':side,'features':FEATURES,'model':m,'trainingWindowEnds':sorted(tr.windowEndMs.astype(int).unique()),'trainingMaxEndMs':int(tr.windowEndMs.max())},art)
  rr={'trainMarkets':int(tr.marketId.nunique()),'validationMarkets':int(va.marketId.nunique()),'train':{'baseHazard':metrics(tr[lab],tr[basecol]),'corrective':metrics(tr[lab],m.predict_proba(Xtr)[:,1])},'validation':{'baseHazard':metrics(va[lab],va[basecol]),'corrective':metrics(va[lab],pp)},'topTerms':top(m),'artifact':str(art)}; rr['validation']['deltaAuc']=rr['validation']['corrective']['auc']-rr['validation']['baseHazard']['auc']; rr['validation']['deltaAP']=rr['validation']['corrective']['ap']-rr['validation']['baseHazard']['ap']; result[side]=rr; print(json.dumps({side:rr},ensure_ascii=False,indent=2),flush=True)
 rep={'reportVersion':'TARGET_MAKER_EARLY_HFT_CORRECTIVE_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'goal':'DAgger-like behavior distillation: on target-blind OUR closed-loop states, predict the same-market Target next-1s Maker side action using only OUR runtime-reconstructable own/public state.','trajectoryPolicy':'V2 PROMOTED_GUARD + HftBacktest actual fills from the first closed-loop stage; no burst layer','trainingCohorts':'early-HFT V2 start0-29','validationCohort':'early-HFT V2 start30-39','finalHoldoutReserved':'early-HFT V2 start40+ untouched by this training/validation','teacherLabel':'high-confidence Target same-side placement_first_ms in (OUR checkpoint, checkpoint+1s]','features':FEATURES,'coverage':{'rows':len(d),'trainRows':int((d.split=='train').sum()),'validationRows':int((d.split=='validation').sum()),'trainMarkets':int(d[d.split=='train'].marketId.nunique()),'validationMarkets':int(d[d.split=='validation'].marketId.nunique())},'results':result,'guards':['No winner/PnL/settlement input.','Target future placement is label only.','Student features are OUR own-state + public book + frozen pressure outputs.','Fixed architecture and one validation cohort; no threshold sweep.','Do not promote unless both side models show meaningful chronological validation lift over frozen base Maker hazard.']}; REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'report':str(REPORT)},indent=2))
if __name__=='__main__':main()
