from __future__ import annotations
import json,lzma,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,recall_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as additive_sim
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as reservation_sim
from tools import run_r4_p0b_role_group_branches_v1 as base
P=ROOT/'data/research/r4_v0/p0_provenance_v1'; SRC=ROOT/'data/hft_forward_paper_v1/markets'
DEV=P/'r4_p0b_stage3_expanded48_dataset_v2.csv'; OUT=P/'r4_p0b_stage3_additive_admission_untouched16_score_v1.json'
F=['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic','candidateReservedQty','candidateReservedRootCount']
EPS=1e-9

def candidates():
 out=[]
 for s in range(0,120,20):
  x=json.loads((P/f'r4_p0b_stage3_additive_admission_candidate_discovery_{s}_20_v1.json').read_text(encoding='utf-8'))
  for r in x['rows']:
   for op in r['opportunities']: out.append({'marketId':int(r['marketId']),'op':op})
 return out

def label_rows():
 rows=[]
 for c in candidates():
  mid=c['marketId']; b=c['op']; key=b['candidateKey']
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
  res=reservation_sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True)
  add=additive_sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
  ao=base.match_op(add,key)
  prefix=bool(ao and all(b.get(k)==ao.get(k) for k in base.PREFIX_KEYS))
  rf={k:float(res['final'][k]) for k in ('floor','absNet','upside')}; af={k:float(add['final'][k]) for k in ('floor','absNet','upside')}
  y=int(af['floor']>=rf['floor']-EPS and af['absNet']<=rf['absNet']+EPS and (af['floor']>rf['floor']+EPS or af['absNet']<rf['absNet']-EPS))
  z=dict(b); z.update({'marketId':mid,'candidateKey':key,'prefixExact':prefix,'reservationFinal':rf,'additiveFinal':af,'y':y})
  rows.append(z); print(json.dumps({'marketId':mid,'candidateKey':key,'prefixExact':prefix,'y':y}),flush=True)
 return rows

def train_dev():
 d=pd.read_csv(DEV).replace([np.inf,-np.inf],np.nan)
 d['candidatePxMinusSideMid']=d.candidatePx-np.where(d.candidateSide.astype(str).eq('UP'),d.predictUpMidPublic,d.predictDownMidPublic)
 y=((d.target_add_floor_gain>=-EPS)&(d.target_add_absnet_gain>=-EPS)&((d.target_add_floor_gain>EPS)|(d.target_add_absnet_gain>EPS))).astype(int)
 m=Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',max_iter=2000,random_state=51000))])
 m.fit(d[F],y); return m

def main():
 rows=label_rows(); valid=[x for x in rows if x['prefixExact']]
 v=pd.DataFrame(valid).replace([np.inf,-np.inf],np.nan)
 v['candidatePxMinusSideMid']=v.candidatePx-np.where(v.candidateSide.astype(str).eq('UP'),v.predictUpMidPublic,v.predictDownMidPublic)
 y=v.y.astype(int).to_numpy(); mdl=train_dev(); prob=mdl.predict_proba(v[F])[:,1]; q=(prob>=.5).astype(int)
 metrics={'nValid':len(v),'positive':int(y.sum()),'negative':int((1-y).sum())}
 if len(set(y))==2:
  metrics.update({'balancedAccuracy':float(balanced_accuracy_score(y,q)),'auc':float(roc_auc_score(y,prob)),'ap':float(average_precision_score(y,prob)),'positiveRecall':float(recall_score(y,q,pos_label=1)),'negativeRecall':float(recall_score(y,q,pos_label=0))})
 samplePass=len(v)>=12 and int(y.sum())>=4 and int((1-y).sum())>=4
 gatePass=samplePass and metrics.get('balancedAccuracy',0)>=.60 and metrics.get('auc',0)>=.63 and metrics.get('positiveRecall',0)>=.50 and metrics.get('negativeRecall',0)>=.50
 scored=[]
 for i,x in enumerate(valid): scored.append({'marketId':x['marketId'],'candidateKey':x['candidateKey'],'label':int(y[i]),'prob':float(prob[i]),'pred':int(q[i]),'reservationFinal':x['reservationFinal'],'additiveFinal':x['additiveFinal']})
 out={'version':'R4_P0B_STAGE3_ADDITIVE_ADMISSION_UNTOUCHED16_SCORE_V1','contract':'r4_p0b_stage3_additive_admission_untouched16_validation_contract_v1.json','labelsOpened':True,'consumed':True,'model':'frozen LOCAL_ECON_EXEC_V0 Logistic C=.5 threshold=.5','metrics':metrics,'sampleValidityPassed':samplePass,'promotionGatePassed':bool(gatePass),'scored':scored}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__':main()
