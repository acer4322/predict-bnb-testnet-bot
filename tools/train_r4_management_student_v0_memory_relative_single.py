from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';S=ROOT/'data/research/supervisor_options_v0';D=S/'supervisor_target_act_states_v2.csv';FRESH=P/'r4_management_student_v0_fresh13_pilot_rows.csv'
DROP={'market_id','market_end_ms','checkpoint_ms','gate_act','option_mode_v2','taker_next3s_raw'}
KEEP={'seconds_left','maker_imbalance_ratio','maker_paired_coverage','taker_imbalance_ratio','taker_paired_coverage','combined_imbalance_ratio','combined_paired_coverage','maker_taker_net_same_sign','up_bid','up_ask','up_spread_ticks','down_bid','down_ask','down_spread_ticks','pair_bid_edge','pair_ask_edge','dominant_opp_bid_pair_edge','last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','last_place_age_ms'}
def sc(y,p):
 y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);r={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'perClass':{}}
 for c in ['HOLD','MAKER','TAKER']:
  z=y==c;r['perClass'][c]={'n':int(z.sum()),'recall':float(np.mean(p[z]==c)) if z.any() else None}
 return r
def main():
 d=pd.read_csv(D);order=d.groupby('market_id').market_end_ms.min().sort_values().index.astype(int).tolist();tr=d[d.market_id.isin(set(order[:-80]))].copy();te=d[d.market_id.isin(set(order[-80:]))].copy();fr=pd.read_csv(FRESH);allf=[c for c in d.columns if c not in DROP];f=[c for c in allf if c in KEEP or '_delta' in c or 'coverage' in c or 'imbalance_ratio' in c or 'pair_edge' in c];y=tr.option_mode_v2.astype(str);vc=y.value_counts();w=y.map({c:len(y)/(len(vc)*n) for c,n in vc.items()}).to_numpy(float);m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=140,max_leaf_nodes=15,min_samples_leaf=45,l2_regularization=1,random_state=4102).fit(tr[f],y,sample_weight=w);rep={'version':'R4_MANAGEMENT_STUDENT_V0_MEMORY_RELATIVE_SINGLE','featureCount':len(f),'final80':sc(te.option_mode_v2,m.predict(te[f])),'fresh13':sc(fr.option_mode_v2,m.predict(fr[f]))};(P/'r4_management_student_v0_memory_relative_single_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
