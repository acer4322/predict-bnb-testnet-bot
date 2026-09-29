from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
FIX=ROOT/'data/research/r4_v0/p0_prep_v1/r4_p0_fixed_cohorts_v1.json';SRC=ROOT/'data/hft_forward_paper_v1/markets'
def core(x): return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cohort-key',required=True);ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 fc=json.loads(FIX.read_text(encoding='utf-8')); ids=[int(x) for x in fc[a.cohort_key]['markets']][a.start:a.start+a.count]
 rows=[];market=[];exact=0
 for mid in ids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
  b0=base.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True)
  ce=core(b0)==core(r);exact+=int(ce);lr=r.get('managementLifecycleRows') or [];rows.extend(lr);market.append({'marketId':mid,'executionCoreExact':ce,'lifecycleRows':len(lr),'roots':len(set(str(x.get('checkpointResponsibilityId')) for x in lr))})
  print(json.dumps(market[-1]),flush=True)
 first={}
 for r in rows:
  k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
  if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
 rep={'cohortKey':a.cohort_key,'start':a.start,'count':len(ids),'executionExact':exact,'marketRows':market,'firstRows':list(first.values()),'outcomes':dict(Counter(str(r.get('rootLifecycleOutcome5s')) for r in first.values()))}
 out=ROOT/a.out;out.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':a.out,'markets':len(ids),'exact':exact,'firstRoots':len(first),'outcomes':rep['outcomes']}))
if __name__=='__main__':main()
