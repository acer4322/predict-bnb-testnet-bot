from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
FIX=ROOT/'data/research/r4_v0/p0_prep_v1/r4_p0_fixed_cohorts_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'

def core(x):
    return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cohort-key',required=True);ap.add_argument('--out',required=True);ap.add_argument('--policy',default='ROLL_KEEP_GAP_OWNER');a=ap.parse_args()
    fc=json.loads(FIX.read_text(encoding='utf-8')); ids=[int(x) for x in fc[a.cohort_key]['markets']]
    rows=[];market=[];exact=0
    for mid in ids:
        p=SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz'
        d=json.load(lzma.open(p,'rt',encoding='utf-8'))
        b0=base.simulate(d,a.policy,collect_shadow=False)
        r=sim.simulate(d,a.policy,collect_shadow=True,collect_provenance=True)
        ce=core(b0)==core(r);exact+=int(ce); lr=r.get('managementLifecycleRows') or [];rows.extend(lr)
        market.append({'marketId':mid,'executionCoreExact':ce,'lifecycleRows':len(lr),'roots':len(set(str(x.get('checkpointResponsibilityId')) for x in lr))})
        print(json.dumps(market[-1],ensure_ascii=False),flush=True)
    first={}
    for r in rows:
        k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
        if k not in first or int(r['t'])<int(first[k]['t']): first[k]=r
    outcome=Counter(str(r.get('rootLifecycleOutcome5s')) for r in first.values())
    rep={'version':'R4_P0B_LIFECYCLE_DATASET_COHORT_V1','cohortKey':a.cohort_key,'policy':a.policy,'cohort':{'markets':len(ids),'marketsWithRows':len(set(int(r['marketId']) for r in rows)),'lifecycleRows':len(rows),'firstCheckpointRoots':len(first)},'integrity':{'executionCoreExact':f'{exact}/{len(ids)}','duplicateLifecycleKeyCount':len(rows)-len({(int(r['marketId']),int(r['t']),str(r['checkpointResponsibilityId'])) for r in rows})},'firstCheckpointOutcomeDistribution':dict(outcome),'marketRows':market,'rows':rows,'guards':['research-only','no action authority','2026-08-16 sealed']}
    out=ROOT/a.out;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':a.out,'cohort':rep['cohort'],'integrity':rep['integrity'],'outcomes':rep['firstCheckpointOutcomeDistribution']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
