from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1';S=ROOT/'data/research/supervisor_options_v0'
D=S/'supervisor_target_act_states_v2.csv';FRESH=P/'r4_management_student_v0_fresh13_pilot_rows.csv'
DROP_BASE={'market_id','market_end_ms','checkpoint_ms','gate_act','option_mode_v2','taker_next3s_raw'}
ABS_DROP_TOKENS=('gross','shares_','depth','placements_')
KEEP_EXACT={'seconds_left','maker_imbalance_ratio','maker_paired_coverage','taker_imbalance_ratio','taker_paired_coverage','combined_imbalance_ratio','combined_paired_coverage','maker_taker_net_same_sign','up_bid','up_ask','up_spread_ticks','down_bid','down_ask','down_spread_ticks','pair_bid_edge','pair_ask_edge','dominant_opp_bid_pair_edge','last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','last_place_age_ms'}
def model(seed):return HistGradientBoostingClassifier(learning_rate=.07,max_iter=140,max_leaf_nodes=15,min_samples_leaf=45,l2_regularization=1,random_state=seed)
def score(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);o={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'perClass':{}}
 for c in ['HOLD','MAKER','TAKER']:
  z=y==c;o['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(p[z]==c)) if z.any() else None}
 return o
def main():
 d=pd.read_csv(D);order=d.groupby('market_id').market_end_ms.min().sort_values().index.astype(int).tolist();train=set(order[:-80]);test=set(order[-80:]);tr=d[d.market_id.isin(train)].copy();te=d[d.market_id.isin(test)].copy();fresh=pd.read_csv(FRESH).copy();allf=[c for c in d.columns if c not in DROP_BASE]
 robust=[c for c in allf if not any(tok in c for tok in ABS_DROP_TOKENS)]
 memrel=[c for c in robust if c in KEEP_EXACT or '_delta' in c or 'coverage' in c or 'imbalance_ratio' in c or 'pair_edge' in c]
 variants={'FULL':allf,'SCALE_ROBUST':robust,'MEMORY_RELATIVE':memrel};res={};arts={}
 ytr=tr.option_mode_v2.astype(str);freq=ytr.value_counts();w=ytr.map({c:len(ytr)/(len(freq)*n) for c,n in freq.items()}).astype(float).to_numpy()
 for i,(name,feats) in enumerate(variants.items(),1):
  m=model(4000+i).fit(tr[feats],ytr,sample_weight=w);res[name]={'featureCount':len(feats),'final80':score(te.option_mode_v2,m.predict(te[feats])),'fresh13':score(fresh.option_mode_v2,m.predict(fresh[feats]))};arts[name]={'model':m,'features':feats}
 fullf=res['FULL']['final80']['balancedAccuracy'];fullx=res['FULL']['fresh13']['balancedAccuracy'];checks={}
 for n in ['SCALE_ROBUST','MEMORY_RELATIVE']:
  checks[n]={'final80NotWorse03':res[n]['final80']['balancedAccuracy']>=fullf-.03,'freshImprove05':res[n]['fresh13']['balancedAccuracy']>=fullx+.05}
 winner=next((n for n in ['SCALE_ROBUST','MEMORY_RELATIVE'] if all(checks[n].values())),None)
 rep={'version':'R4_MANAGEMENT_STUDENT_V0_SCALE_ROBUST','researchOnly':True,'results':res,'checks':checks,'winner':winner};(P/'r4_management_student_v0_scale_robust_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');joblib.dump({'version':rep['version'],'winner':winner,'variants':arts,'researchOnly':True,'actionAuthority':False},P/'r4_management_student_v0_scale_robust.joblib');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
