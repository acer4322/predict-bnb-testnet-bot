from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as sim
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';EPS=1e-9
DATA=P/'r4_p0b_stage3_expanded48_dataset_v2.csv'
ROLES={'PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING'}

def snap(pref,parent_oid,side,cut):
    pp=[e for e in pref if int(e.get('received_at_ms') or 0)<=int(cut)]
    st=led.replay_objective_journal(pp)
    active={k:v for k,v in st.items() if float(v.get('residual_objective_deficit_qty') or 0)>EPS or float(v.get('reserved_same_objective_commitment_qty') or 0)>EPS}
    ss=[v for v in active.values() if str(v.get('side'))==side];os=[v for v in active.values() if str(v.get('side'))!=side]
    po=st.get(parent_oid) or {}
    return {
      'same_active':float(len(ss)),'opp_active':float(len(os)),
      'same_residual':float(sum(float(v.get('residual_objective_deficit_qty') or 0.) for v in ss)),
      'opp_residual':float(sum(float(v.get('residual_objective_deficit_qty') or 0.) for v in os)),
      'parent_residual':float(po.get('residual_objective_deficit_qty') or 0.),
      'parent_reserved':float(po.get('reserved_same_objective_commitment_qty') or 0.),
      'parent_confirmed':float(po.get('confirmed_same_objective_completion_qty') or 0.),
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args()
    d=pd.read_csv(DATA);d=d[d.knownRole.isin(ROLES)].reset_index(drop=True);sub=d.iloc[a.start:a.start+a.count]
    out=[]
    for _,r in sub.iterrows():
        mid=int(r.marketId);key=str(r.candidateKey);t=int(r.candidateT);side=str(r.candidateSide);parent=str(r.parentLogical);poid=led.oid(mid,parent)
        z=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
        rr=sim.simulate(z,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
        j,_,_=led.materialize(mid,rr.get('provenanceJournal') or [])
        idx=None
        for i,e in enumerate(j):
            if e.get('event_type')=='OBJECTIVE_OPENED' and e.get('parent_objective_id')==poid and abs(int(e.get('received_at_ms') or 0)-t)<=1:
                idx=i;break
        if idx is None:
            x={'marketId':mid,'candidateKey':key,'knownRole':str(r.knownRole),'error':'NO_CANDIDATE_OBJECTIVE_OPEN'};out.append(x);print(json.dumps(x),flush=True);continue
        pref=j[:idx]
        cur=snap(pref,poid,side,t);s5=snap(pref,poid,side,t-5000);s15=snap(pref,poid,side,t-15000)
        q=max(EPS,abs(float(r.candidateQty)));gap=max(EPS,abs(float(r.candidateGap)))
        same=float(r.same_side_total_residual or 0.);opp=float(r.opposite_side_total_residual or 0.);pres=float(r.parent_residual or 0.);prsv=float(r.parent_reserved or 0.);pconf=float(r.parent_confirmed or 0.);cr=float(r.candidateReservedQty or 0.)
        x={
          'marketId':mid,'candidateKey':key,'knownRole':str(r.knownRole),'candidateQty':q,'candidateGap':gap,
          'candidate_to_gap':q/gap,'parent_residual_units':pres/q,'parent_reserved_units':prsv/q,'parent_confirmed_units':pconf/q,
          'same_side_residual_units':same/q,'opposite_side_residual_units':opp/q,'candidate_reserved_units':cr/q,
          'same_side_unowned_residual_units':max(0.,same-pres)/q,'existing_credit_capacity_ratio':min(1.,max(0.,same)/q),
          'parent_credit_capacity_ratio':min(1.,max(0.,pres)/q),'residual_excess_units':(same-q)/q,
        }
        for name,nowv,pastv in [('parent_residual',cur['parent_residual'],None),('parent_reserved',cur['parent_reserved'],None),('parent_confirmed',cur['parent_confirmed'],None),('same_side_residual',cur['same_residual'],None),('opposite_side_residual',cur['opp_residual'],None)]:
            key5={'parent_residual':'parent_residual','parent_reserved':'parent_reserved','parent_confirmed':'parent_confirmed','same_side_residual':'same_residual','opposite_side_residual':'opp_residual'}[name]
            x[f'{name}_delta5_units']=(float(nowv)-float(s5[key5]))/q
            x[f'{name}_delta15_units']=(float(nowv)-float(s15[key5]))/q
        x['same_side_active_delta5']=cur['same_active']-s5['same_active'];x['same_side_active_delta15']=cur['same_active']-s15['same_active']
        x['opposite_side_active_delta5']=cur['opp_active']-s5['opp_active'];x['opposite_side_active_delta15']=cur['opp_active']-s15['opp_active']
        # consistency checks: replay current should match dataset current context except tiny floating error
        x['sameResidualReplayError']=cur['same_residual']-same;x['parentResidualReplayError']=cur['parent_residual']-pres
        out.append(x);print(json.dumps({'marketId':mid,'role':x['knownRole'],'sameD5':x['same_side_residual_delta5_units'],'parentConfD15':x['parent_confirmed_delta15_units'],'replayErr':x['sameResidualReplayError']}),flush=True)
    path=P/f'r4_management_marginal_objective_value_obligation_trajectory_chunk_{a.start}_{a.count}_v1.json'
    path.write_text(json.dumps({'version':'R4_MANAGEMENT_MARGINAL_OBJECTIVE_VALUE_OBLIGATION_TRAJECTORY_CHUNK_V1','start':a.start,'count':len(sub),'rows':out},indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(path.relative_to(ROOT)),'rows':len(out),'errors':sum('error' in x for x in out)}))
if __name__=='__main__':main()
