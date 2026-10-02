from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as additive_sim
from tools import test_r4_p0b_successor_credit_probe_simulator_v1 as credit_sim
from tools import run_r4_p0b_role_group_branches_v1 as base
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
from tools import test_r4_p0b_objective_context_memory_v1 as ctxm
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets'

def candidates():
 out=[]
 for start in range(0,240,30):
  q=P/f'r4_p0b_objective_grouping_replication_discovery_{start}_30_v1.json';d=json.loads(q.read_text(encoding='utf-8'))
  for m in d['rows']:
   for op in m['opportunities']:out.append({'marketId':int(m['marketId']),'baselineOp':op,'baselineCore':m['baselineCore']})
 return sorted(out,key=lambda x:(x['marketId'],int(x['baselineOp']['candidateT']),str(x['baselineOp']['candidateKey'])),reverse=True)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args();cs=candidates()[a.start:a.start+a.count];rows=[]
 for c in cs:
  mid=c['marketId'];b=c['baselineOp'];key=b['candidateKey'];d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
  add=additive_sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key);cred=credit_sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
  ao=base.match_op(add,key);co=base.match_op(cred,key);apx=all(b.get(k)==ao.get(k) for k in base.PREFIX_KEYS);cpx=all(b.get(k)==co.get(k) for k in base.PREFIX_KEYS);ai=base.succ_info(add,b);ci=base.succ_info(cred,b);samefill=abs(ai['fillQty']-ci['fillQty'])<=base.EPS and ai['firstFillMs']==ci['firstFillMs'];valid=bool(apx and cpx and samefill)
  bf={k:float(c['baselineCore']['final'][k]) for k in ('floor','absNet','upside')};af={k:float(add['final'][k]) for k in ('floor','absNet','upside')};cf={k:float(cred['final'][k]) for k in ('floor','absNet','upside')};role=base.role_label(bf,af,cf,ai['fillQty']>base.EPS,valid)
  context=None
  if valid:
   j,_,_=led.materialize(mid,add.get('provenanceJournal') or []);poid=led.oid(mid,str(b.get('parentLogical')));t=int(b['candidateT']);side=str(b['candidateSide']);idx=None
   for i,e in enumerate(j):
    if e.get('event_type')=='OBJECTIVE_OPENED' and e.get('parent_objective_id')==poid and abs(int(e.get('received_at_ms') or 0)-t)<=1:idx=i;break
   if idx is not None:context=ctxm.ctx_before(j,idx,poid,side,t)
  rows.append({'marketId':mid,'candidateKey':key,'branchValid':valid,'role':role,'fillQty':ai['fillQty'],'context':context,'final':{'RESERVATION':bf,'ADDITIVE':af,'CREDIT':cf}});print(json.dumps({'marketId':mid,'role':role,'valid':valid,'fill':ai['fillQty'],'ctx':None if context is None else {k:context[k] for k in ['same_side_active_objectives','same_side_total_residual','same_side_oldest_objective_age_s']}},ensure_ascii=False),flush=True)
 q=P/f'r4_p0b_objective_grouping_context_replication_branches_{a.start}_{len(cs)}_v1.json';q.write_text(json.dumps({'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(q.relative_to(ROOT)),'rows':len(rows),'valid':sum(r['branchValid'] for r in rows)},ensure_ascii=False))
if __name__=='__main__':main()
