from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as probe
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as reservation
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
PRE=P/'r4_p0b_role_group_cohorts_preregistered_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'

def core(r):
    return {'makerFilledShares':r['makerFilledShares'],'durableBase':r['durableBase'],'final':r['final']}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--split',choices=['development','independentReplication'],required=True);ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args()
    pre=json.loads(PRE.read_text(encoding='utf-8')); ids=pre[a.split][a.start:a.start+a.count]
    rows=[];exact=0;ops=0
    for mid in ids:
        d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
        b=reservation.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True)
        n=probe.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='NONE')
        eq=core(b)==core(n); exact+=int(eq)
        oo=n.get('successorOpportunityRows') or [];ops+=len(oo)
        rows.append({'marketId':mid,'executionEquivalent':eq,'baselineCore':core(b),'opportunities':oo})
        print(json.dumps({'marketId':mid,'executionEquivalent':eq,'opportunities':len(oo)},ensure_ascii=False),flush=True)
    out=P/f'r4_p0b_role_group_discovery_{a.split}_{a.start}_{a.count}_v1.json'
    out.write_text(json.dumps({'split':a.split,'ids':ids,'executionExact':exact,'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(out.relative_to(ROOT)),'markets':len(ids),'executionExact':exact,'opportunities':ops},ensure_ascii=False))
if __name__=='__main__':main()
