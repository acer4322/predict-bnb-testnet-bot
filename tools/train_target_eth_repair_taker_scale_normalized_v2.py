from __future__ import annotations
import argparse,json,math,importlib.util
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,brier_score_loss

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('base',HERE/'train_target_eth_repair_taker_escalation_hazard_v1.py');base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
EPS=1e-9
NORM_FEATURES=[
 'seconds_left','maker_imbalance_ratio','maker_paired_coverage','combined_imbalance_ratio','combined_paired_coverage',
 'floor_per_gross','best_per_gross','payoff_gap_per_gross','maker_absnet_change_10s_norm','combined_absnet_change_10s_norm',
 'last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_3s','maker_fills_5s','maker_fills_10s','maker_side_streak',
 'maker_shares_3s_frac','maker_shares_5s_frac','maker_shares_10s_frac',
 'maker_repair_parents_1s','maker_repair_parents_3s','maker_repair_parents_5s','maker_repair_parents_10s',
 'maker_repair_shares_1s_frac','maker_repair_shares_3s_frac','maker_repair_shares_5s_frac','maker_repair_shares_10s_frac',
 'latest_maker_expansion_age_ms','latest_maker_expansion_repaid_frac',
 'repair_bid','repair_ask','repair_spread_ticks','dominant_bid','dominant_ask','pair_bid_edge','pair_ask_edge',
 'repair_bid_depth_gap_log','repair_ask_depth_gap_log','repair_top3_bid_depth_gap_log','dominant_bid_depth_gap_log','dominant_top3_bid_depth_gap_log'
]

def rownorm(r):
 mg=max(float(r.get('maker_gross') or 0),EPS);cg=max(float(r.get('combined_gross') or 0),EPS);gap=max(float(r.get('maker_abs_net') or 0),EPS);net=float(r.get('maker_net') or 0)
 if net>=0:
  rb=float(r.get('down_bid') or 0);ra=float(r.get('down_ask') or 0);rs=float(r.get('down_spread_ticks') or 0);rbd=float(r.get('down_bid_depth') or 0);rad=float(r.get('down_ask_depth') or 0);rt=float(r.get('down_top3_bid_depth') or 0);db=float(r.get('up_bid_depth') or 0);dt=float(r.get('up_top3_bid_depth') or 0)
 else:
  rb=float(r.get('up_bid') or 0);ra=float(r.get('up_ask') or 0);rs=float(r.get('up_spread_ticks') or 0);rbd=float(r.get('up_bid_depth') or 0);rad=float(r.get('up_ask_depth') or 0);rt=float(r.get('up_top3_bid_depth') or 0);db=float(r.get('down_bid_depth') or 0);dt=float(r.get('down_top3_bid_depth') or 0)
 return {
  'seconds_left':float(r['seconds_left']),'maker_imbalance_ratio':float(r['maker_imbalance_ratio']),'maker_paired_coverage':float(r['maker_paired_coverage']),'combined_imbalance_ratio':float(r['combined_imbalance_ratio']),'combined_paired_coverage':float(r['combined_paired_coverage']),
  'floor_per_gross':float(r['worst_case_floor'])/cg,'best_per_gross':float(r['best_case_pnl'])/cg,'payoff_gap_per_gross':float(r['abs_payoff_gap'])/cg,'maker_absnet_change_10s_norm':float(r['maker_absnet_change_10s'])/mg,'combined_absnet_change_10s_norm':float(r['combined_absnet_change_10s'])/cg,
  'last_maker_age_ms':r.get('last_maker_age_ms'),'last_maker_up_age_ms':r.get('last_maker_up_age_ms'),'last_maker_down_age_ms':r.get('last_maker_down_age_ms'),'maker_fills_1s':float(r['maker_fills_1s']),'maker_fills_3s':float(r['maker_fills_3s']),'maker_fills_5s':float(r['maker_fills_5s']),'maker_fills_10s':float(r['maker_fills_10s']),'maker_side_streak':float(r['maker_side_streak']),
  'maker_shares_3s_frac':float(r['maker_shares_3s'])/mg,'maker_shares_5s_frac':float(r['maker_shares_5s'])/mg,'maker_shares_10s_frac':float(r['maker_shares_10s'])/mg,
  'maker_repair_parents_1s':float(r['maker_repair_parents_1s']),'maker_repair_parents_3s':float(r['maker_repair_parents_3s']),'maker_repair_parents_5s':float(r['maker_repair_parents_5s']),'maker_repair_parents_10s':float(r['maker_repair_parents_10s']),
  'maker_repair_shares_1s_frac':float(r['maker_repair_shares_1s'])/mg,'maker_repair_shares_3s_frac':float(r['maker_repair_shares_3s'])/mg,'maker_repair_shares_5s_frac':float(r['maker_repair_shares_5s'])/mg,'maker_repair_shares_10s_frac':float(r['maker_repair_shares_10s'])/mg,
  'latest_maker_expansion_age_ms':r.get('latest_maker_expansion_age_ms'),'latest_maker_expansion_repaid_frac':r.get('latest_maker_expansion_repaid_frac'),
  'repair_bid':rb,'repair_ask':ra,'repair_spread_ticks':rs,'dominant_bid':float(r.get('dominant_bid') or 0),'dominant_ask':float(r.get('dominant_ask') or 0),'pair_bid_edge':float(r['pair_bid_edge']),'pair_ask_edge':float(r['pair_ask_edge']),
  'repair_bid_depth_gap_log':math.log1p(max(0,rbd)/gap),'repair_ask_depth_gap_log':math.log1p(max(0,rad)/gap),'repair_top3_bid_depth_gap_log':math.log1p(max(0,rt)/gap),'dominant_bid_depth_gap_log':math.log1p(max(0,db)/gap),'dominant_top3_bid_depth_gap_log':math.log1p(max(0,dt)/gap)
 }

def mat(rows):
 vals=[rownorm(r) for r in rows];X=np.asarray([[np.nan if z.get(k) is None else float(z.get(k)) for k in NORM_FEATURES] for z in vals],np.float32);y=np.asarray([int(r['label_repair_taker_1s']) for r in rows],int);return X,y

def split(rows):
 mids=sorted({int(r['market_id']) for r in rows});a=max(1,int(len(mids)*.6));b=max(a+1,int(len(mids)*.8));tr=set(mids[:a]);va=set(mids[a:b]);te=set(mids[b:]);return {k:[r for r in rows if int(r['market_id']) in s] for k,s in [('train',tr),('validation',va),('test',te)]},{'trainMarkets':len(tr),'validationMarkets':len(va),'testMarkets':len(te),'trainMax':max(tr) if tr else None,'validationRange':[min(va),max(va)] if va else None,'testMin':min(te) if te else None}
def metrics(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);ix=np.argsort(-p);k=max(1,int(math.ceil(len(y)*.1))) if len(y) else 0;return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'brier':float(brier_score_loss(y,p)) if len(y) else None,'top10pctPositiveRate':float(y[ix[:k]].mean()) if k else None,'top10pctRecall':float(y[ix[:k]].sum()/y.sum()) if y.sum()>0 else None,'meanP':float(np.mean(p)) if len(p) else None}
def base_mat(rows,features):return np.asarray([[np.nan if r.get(k) is None else float(r.get(k)) for k in features] for r in rows],np.float32)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--baseline-model',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-output',required=True);a=ap.parse_args();allrows=base.build(a.db)
 rows=[r for r in allrows if float(r.get('taker_gross') or 0)<=EPS and r.get('latest_maker_expansion_age_ms') is not None and math.isfinite(float(r.get('latest_maker_expansion_age_ms'))) and float(r.get('maker_abs_net') or 0)>EPS]
 parts,sp=split(rows);Xtr,ytr=mat(parts['train']);Xv,yv=mat(parts['validation']);Xt,yt=mat(parts['test'])
 model=HistGradientBoostingClassifier(max_iter=300,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=30,l2_regularization=2.0,class_weight='balanced',random_state=9402).fit(Xtr,ytr)
 art1=joblib.load(a.baseline_model);bf=list(art1['features']);bm=art1['model']
 out={'version':'TARGET_ETH_REPAIR_TAKER_ESCALATION_SCALE_NORMALIZED_V2','researchOnly':True,'coverage':{'sourceRows':len(allrows),'domainRows':len(rows),'markets':len({int(r['market_id']) for r in rows})},'split':sp,'features':NORM_FEATURES,'metrics':{},'baselineV1SameDomain':{},'modelOutput':a.model_output,'boundary':['Fresh ETH Target only.','Zero-Taker post-expansion unresolved states.','Scale-normalized own-state + public-book geometry only.','No winner/PnL.','No BTC transfer.','No OUR outcome used in fit.','No runtime authority.']}
 for name,X,y,rr in [('train',Xtr,ytr,parts['train']),('validation',Xv,yv,parts['validation']),('test',Xt,yt,parts['test'])]:
  p=model.predict_proba(X)[:,1];pb=bm.predict_proba(base_mat(rr,bf))[:,1];out['metrics'][name]=metrics(y,p);out['baselineV1SameDomain'][name]=metrics(y,pb)
 joblib.dump({'version':'TARGET_ETH_REPAIR_TAKER_ESCALATION_SCALE_NORMALIZED_V2','features':NORM_FEATURES,'model':model,'transform':'rownorm_v1'},a.model_output);Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
