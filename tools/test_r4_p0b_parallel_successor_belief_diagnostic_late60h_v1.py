from __future__ import annotations
import json,lzma,joblib,sys
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score
from scipy.stats import spearmanr
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as reserve
from tools import test_r4_p0b_legitimate_successor_simulator_v1 as succ
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_parallel_successor_belief_diagnostic_late60h_preregistered_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_parallel_successor_belief_diagnostic_late60h_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'; EPS=1e-9
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib')
TRANS=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib')
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);o={'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
 if len(y) and len(np.unique(y))>1:o.update({'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p))})
 else:o.update({'auc':None,'ap':None})
 return o
def main():
 # derive eligible markets from the frozen Late60h outputs; no outcome-based selection beyond natural successor trigger
 rows=[]
 for f in sorted((ROOT/'data/research/r4_v0/p0_provenance_v1').glob('r4_p0b_legitimate_successor_late60h_chunk_*.json')):
  rows.extend(json.loads(f.read_text(encoding='utf-8'))['rows'])
 ids=[int(r['marketId']) for r in rows if int(r['variant'].get('successorSubmits',0))>0]
 mf=list(STACK['features']['full']); mm=STACK['M0_model']; tf=list(TRANS['features']); tm=TRANS['model']; out=[]
 for mid in ids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
  b=reserve.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);v=succ.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True)
  sr=next((r for r in v.get('managementShadowRows',[]) if str(r.get('kind'))=='SUCCESSOR_OPTION'),None)
  if sr is None:continue
  xm=np.asarray([[float(sr[f]) for f in mf]],float);xt=np.asarray([[float(sr[f]) for f in tf]],float)
  m0=float(mm.predict_proba(xm)[0,1]);tr=float(tm.predict_proba(xt)[0,1]);fd=float(v['final']['floor'])-float(b['final']['floor']);ad=float(v['final']['absNet'])-float(b['final']['absNet'])
  safe=int(fd>=-EPS and ad<=EPS);harm=int(fd<-EPS)
  out.append({'marketId':mid,'t':int(sr['t']),'m0EconomicProgress':m0,'inverseTransitionProgress':1.0-tr,'transitionNonprogressRisk':tr,'safeSuccessor':safe,'floorHarm':harm,'floorDeltaVsReservation':fd,'absNetDeltaVsReservation':ad,'successorSubmits':int(v['counts'].get('legitimateSuccessorSubmits',0))})
 y=[r['safeSuccessor'] for r in out]; yh=[r['floorHarm'] for r in out]; p0=[r['m0EconomicProgress'] for r in out]; pi=[r['inverseTransitionProgress'] for r in out]
 metrics={'SAFE_SUCCESSOR':{'M0_PROGRESS':met(y,p0),'INVERSE_TRANSITION':met(y,pi)},'FLOOR_HARM':{'M0_PROGRESS':met(yh,p0),'INVERSE_TRANSITION':met(yh,pi)}}
 cont={}
 for nm,p in [('M0_PROGRESS',p0),('INVERSE_TRANSITION',pi)]:
  cont[nm]={'spearmanFloorDelta':float(spearmanr(p,[r['floorDeltaVsReservation'] for r in out]).statistic),'spearmanAbsNetDelta':float(spearmanr(p,[r['absNetDeltaVsReservation'] for r in out]).statistic)}
 rep={'version':'R4_P0B_PARALLEL_SUCCESSOR_BELIEF_DIAGNOSTIC_LATE60H_V1','status':'DIAGNOSTIC_COMPLETE','preRegistered':str(PRE.relative_to(ROOT)).replace('\\','/'),'cohort':{'eligibleMarkets':len(out),'marketIds':[r['marketId'] for r in out]},'metrics':metrics,'continuousDiagnostics':cont,'rows':out,'guards':['No threshold sweep','No model training','Handoff excluded: parallel successor semantic','Research-only']}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':rep['status'],'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohort':rep['cohort'],'metrics':metrics,'continuousDiagnostics':cont,'rows':out},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
