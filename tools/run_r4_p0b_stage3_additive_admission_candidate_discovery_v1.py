from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as probe
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as reservation
P=ROOT/'data/research/r4_v0/p0_provenance_v1'; SRC=ROOT/'data/hft_forward_paper_v1/markets'
PRE=P/'r4_p0b_stage3_additive_admission_untouched_cohort_v1_preregistered.json'

def core(r): return {'makerFilledShares':r['makerFilledShares'],'durableBase':r['durableBase'],'final':r['final']}
def strictpast_op(op):
    # Deliberately remove post-candidate realized/future fields. Branch labels remain unopened.
    return {k:v for k,v in op.items() if not k.startswith('realized') and not k.startswith('future')}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--start',type=int,required=True); ap.add_argument('--count',type=int,required=True); a=ap.parse_args()
    ids=json.loads(PRE.read_text(encoding='utf-8'))['cohort'][a.start:a.start+a.count]
    rows=[]; exact=0
    for mid in ids:
        d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
        b=reservation.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True)
        n=probe.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='NONE')
        eq=(core(b)==core(n)); exact+=int(eq)
        ops=[strictpast_op(x) for x in (n.get('successorOpportunityRows') or [])]
        rows.append({'marketId':mid,'executionEquivalent':eq,'opportunities':ops})
        print(json.dumps({'marketId':mid,'executionEquivalent':eq,'opportunities':len(ops)}),flush=True)
    out=P/f'r4_p0b_stage3_additive_admission_candidate_discovery_{a.start}_{a.count}_v1.json'
    payload={'version':'R4_P0B_STAGE3_ADDITIVE_ADMISSION_CANDIDATE_DISCOVERY_V1','labelsOpened':False,'strictPastOnly':True,'ids':ids,'executionExact':exact,'rows':rows}
    out.write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(out.relative_to(ROOT)),'markets':len(ids),'executionExact':exact,'opportunities':sum(len(x['opportunities']) for x in rows)}))
if __name__=='__main__': main()
