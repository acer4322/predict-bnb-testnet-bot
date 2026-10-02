from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_pending_submit_reservation_late20f_preregistered_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
def core(x): return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 ids=[int(x) for x in json.loads(PRE.read_text(encoding='utf-8'))['cohort']][a.start:a.start+a.count]
 rows=[]; exact=0; market=[]
 for mid in ids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
  b=base.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False); r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True)
  ce=core(b)==core(r); exact+=int(ce); lr=r.get('managementLifecycleRows') or []; rows.extend(lr)
  market.append({'marketId':mid,'executionCoreExact':ce,'rows':len(lr)}); print(json.dumps(market[-1]),flush=True)
 rep={'start':a.start,'count':len(ids),'executionExact':exact,'markets':market,'rows':rows}
 out=ROOT/a.out; out.write_text(json.dumps(rep,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'artifact':a.out,'markets':len(ids),'exact':exact,'rows':len(rows)}))
if __name__=='__main__':main()
