from __future__ import annotations
import argparse,json,lzma,sys,psutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
SRC=ROOT/'data/hft_forward_paper_v1/markets'
def core(x):return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--prereg',required=True);ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);ap.add_argument('--out',required=True);a=ap.parse_args();d0=json.loads((ROOT/a.prereg).read_text());ids=[int(x) for x in d0['cohort']][a.start:a.start+a.count]
 rows=[];market=[];exact=0
 for mid in ids:
  cpu=psutil.cpu_percent(.3);ram=psutil.virtual_memory().percent
  if cpu>=75 or ram>=86:raise SystemExit(f'RESOURCE_GUARD cpu={cpu} ram={ram}')
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));b=base.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);ce=core(b)==core(r);exact+=int(ce);lr=r.get('managementLifecycleRows') or [];rows.extend(lr);market.append({'marketId':mid,'executionCoreExact':ce,'rows':len(lr)});print(json.dumps(market[-1]),flush=True)
 (ROOT/a.out).write_text(json.dumps({'prereg':a.prereg,'start':a.start,'count':len(ids),'executionExact':exact,'marketRows':market,'rows':rows},ensure_ascii=False),encoding='utf-8');print(json.dumps({'artifact':a.out,'markets':len(ids),'exact':exact,'rows':len(rows)}))
if __name__=='__main__':main()
