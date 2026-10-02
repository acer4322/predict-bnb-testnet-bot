from __future__ import annotations
import json
from pathlib import Path
import joblib,numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,f1_score,average_precision_score

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'supervisor_options_v0'
SRC=OUT/'student_state_supervisor_v0_states.csv'
REPORT=OUT/'student_state_supervisor_hier_v01_report.json'
ART=OUT/'student_state_supervisor_hier_v01.joblib'
SEED=20260820
CURRENT=[
 'seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign','last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_shares_10s','taker_shares_10s','maker_absnet_change_10s','combined_absnet_change_10s','maker_avg_pair_edge','taker_avg_pair_edge','combined_avg_pair_edge','up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','pair_bid_edge','pair_ask_edge','dominant_opp_bid_pair_edge','last_place_age_ms','placements_1s','placements_5s','placements_10s','placement_side_balance_5s','placement_side_balance_10s']
MEM=[c for c in pd.read_csv(SRC,nrows=1).columns if '_delta' in c]

def numeric(d,fs):return d[fs].apply(pd.to_numeric,errors='coerce')
def usable(d,fs):
 out=[]
 for f in fs:
  z=pd.to_numeric(d[f],errors='coerce').dropna()
  if len(z)>=2 and z.nunique()>=2:out.append(f)
 return out
def weights(y):
 c=y.value_counts();n=len(y);mp={k:np.sqrt(n/max(1,int(v))) for k,v in c.items()};w=y.map(mp).astype(float).to_numpy();return w/w.mean()
def fit(tr,fs,label):
 fs=usable(tr,fs);m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=90,max_leaf_nodes=15,min_samples_leaf=18,l2_regularization=1,early_stopping=True,validation_fraction=.15,n_iter_no_change=15,random_state=SEED);y=tr[label].astype(str);m.fit(numeric(tr,fs),y,sample_weight=weights(y));return m,fs
def met(m,x,fs,label,pos):
 y=x[label].astype(str);cl=list(map(str,m.classes_));pi=cl.index(pos);p=m.predict_proba(numeric(x,fs))[:,pi];pred=pd.Series(m.predict(numeric(x,fs)),index=x.index).eq(pos).astype(int);yy=y.eq(pos).astype(int)
 return {'n':len(x),'rate':float(yy.mean()),'predRate':float(pred.mean()),'auc':float(roc_auc_score(yy,p)) if yy.nunique()>1 else None,'ap':float(average_precision_score(yy,p)) if yy.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,pred)),'f1':float(f1_score(yy,pred,zero_division=0))}
def main():
 d=pd.read_csv(SRC).sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);d['gate_act']=np.where(d.teacher_mode.eq('HOLD'),'HOLD','ACT');d['gate_channel']=np.where(d.teacher_mode.eq('TAKER'),'TAKER','MAKER')
 markets=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=markets.market_id.astype(int).tolist();a=int(len(ids)*.68);b=int(len(ids)*.84);spl={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])};parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in spl.items()};variants={'currentOnly':CURRENT,'currentPlusMemory':CURRENT+MEM};res={};models={}
 for vn,fs in variants.items():
  ma,fa=fit(parts['train'],fs,'gate_act');acttr=parts['train'][parts['train'].gate_act.eq('ACT')];mc,fc=fit(acttr,fs,'gate_channel');models[vn]={'act':ma,'actFeatures':fa,'channel':mc,'channelFeatures':fc};res[vn]={}
  for s in ('validation','test'):
   z=parts[s];az=z[z.gate_act.eq('ACT')];res[vn][s]={'act':met(ma,z,fa,'gate_act','ACT'),'channelTaker':met(mc,az,fc,'gate_channel','TAKER') if len(az) else None,'activeCounts':az.gate_channel.value_counts().to_dict()}
 joblib.dump({'version':'STUDENT_STATE_SUPERVISOR_HIER_V01','researchOnly':True,'runtimePromotion':False,'models':models['currentPlusMemory'],'memorySemantics':'~6/20/60s'},ART)
 rep={'reportVersion':'STUDENT_STATE_SUPERVISOR_HIER_V01','researchOnly':True,'question':'Does binary hierarchical gating avoid rare-TAKER collapse on OUR-state?','source':{'markets':int(d.market_id.nunique()),'rows':len(d),'teacherCounts':d.teacher_mode.value_counts().to_dict()},'splitMarkets':{k:len(v) for k,v in spl.items()},'results':res,'decisionRule':'Only justify larger consistent-R2 trajectory generation if ACT and conditional TAKER gates show directionally stable validation/test discrimination above chance; no PnL used.','artifact':str(ART),'guards':['No winner/PnL','No special 8/16','No final75-99','No runtime changes']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
