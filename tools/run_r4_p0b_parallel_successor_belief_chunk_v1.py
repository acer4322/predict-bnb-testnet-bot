from __future__ import annotations
import argparse,json,lzma,joblib,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as reserve
from tools import test_r4_p0b_legitimate_successor_simulator_v1 as succ
SRC=ROOT/'data/hft_forward_paper_v1/markets'
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib');TRANS=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib')
EPS=1e-9
def eligible_ids():
 rows=[]
 for f in sorted((ROOT/'data/research/r4_v0/p0_provenance_v1').glob('r4_p0b_legitimate_successor_late60h_chunk_*.json')):rows.extend(json.loads(f.read_text(encoding='utf-8'))['rows'])
 return [int(r['marketId']) for r in rows if int(r['variant'].get('successorSubmits',0))>0]
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args();ids=eligible_ids()[a.start:a.start+a.count];mf=list(STACK['features']['full']);mm=STACK['M0_model'];tf=list(TRANS['features']);tm=TRANS['model'];out=[]
 for mid in ids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));b=reserve.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);v=succ.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);sr=next((r for r in v.get('managementShadowRows',[]) if str(r.get('kind'))=='SUCCESSOR_OPTION'),None)
  if sr is None:continue
  m0=float(mm.predict_proba(np.asarray([[float(sr[f]) for f in mf]],float))[0,1]);tr=float(tm.predict_proba(np.asarray([[float(sr[f]) for f in tf]],float))[0,1]);fd=float(v['final']['floor'])-float(b['final']['floor']);ad=float(v['final']['absNet'])-float(b['final']['absNet']);safe=int(fd>=-EPS and ad<=EPS);harm=int(fd<-EPS)
  z={'marketId':mid,'t':int(sr['t']),'m0EconomicProgress':m0,'inverseTransitionProgress':1.0-tr,'transitionNonprogressRisk':tr,'safeSuccessor':safe,'floorHarm':harm,'floorDeltaVsReservation':fd,'absNetDeltaVsReservation':ad,'successorSubmits':int(v['counts'].get('legitimateSuccessorSubmits',0))};out.append(z);print(json.dumps(z,ensure_ascii=False),flush=True)
 p=ROOT/f'data/research/r4_v0/p0_provenance_v1/r4_p0b_parallel_successor_belief_chunk_{a.start}_{a.count}.json';p.write_text(json.dumps({'ids':ids,'rows':out},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(p.relative_to(ROOT)),'rows':len(out)}))
if __name__=='__main__':main()
