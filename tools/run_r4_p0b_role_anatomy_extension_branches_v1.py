from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as additive_sim
from tools import test_r4_p0b_successor_credit_probe_simulator_v1 as credit_sim
from tools import run_r4_p0b_role_group_branches_v1 as base
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets'

def load_candidates():
 out=[]
 for start in range(0,240,20):
  d=json.loads((P/f'r4_p0b_role_anatomy_extension_discovery_{start}_20_v1.json').read_text(encoding='utf-8'))
  for m in d['rows']:
   for op in m['opportunities']:out.append({'marketId':int(m['marketId']),'baselineOp':op,'baselineCore':m['baselineCore']})
 return sorted(out,key=lambda x:(x['marketId'],int(x['baselineOp']['candidateT']),str(x['baselineOp']['candidateKey'])),reverse=True)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args();cs=load_candidates()[a.start:a.start+a.count];rows=[]
 for c in cs:
  mid=c['marketId'];b=c['baselineOp'];key=b['candidateKey'];d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
  add=additive_sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
  cred=credit_sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
  ao=base.match_op(add,key);co=base.match_op(cred,key)
  apx=all(b.get(k)==ao.get(k) for k in base.PREFIX_KEYS);cpx=all(b.get(k)==co.get(k) for k in base.PREFIX_KEYS)
  ai=base.succ_info(add,b);ci=base.succ_info(cred,b);samefill=abs(ai['fillQty']-ci['fillQty'])<=base.EPS and ai['firstFillMs']==ci['firstFillMs'];valid=bool(apx and cpx and samefill)
  basef={k:float(c['baselineCore']['final'][k]) for k in ('floor','absNet','upside')};addf={k:float(add['final'][k]) for k in ('floor','absNet','upside')};credf={k:float(cred['final'][k]) for k in ('floor','absNet','upside')};role=base.role_label(basef,addf,credf,ai['fillQty']>base.EPS,valid)
  row={'marketId':mid,'candidateKey':key,'candidate':{k:b.get(k) for k in b},'prefixExact':{'additive':apx,'credit':cpx},'successorFill':{'additive':ai,'credit':ci,'exact':samefill},'branchValid':valid,'role':role,'final':{'REJECT_NO_ACTION':basef,'ADDITIVE':addf,'CREDIT':credf},'credit':{'events':cred.get('successorCreditEvents') or [],'outstanding':float(cred.get('successorCreditOutstanding') or 0.)}}
  rows.append(row);print(json.dumps({'marketId':mid,'candidateKey':key,'valid':valid,'fill':ai['fillQty'],'role':role,'final':row['final']},ensure_ascii=False),flush=True)
 out=P/f'r4_p0b_role_anatomy_extension_branches_{a.start}_{a.count}_v1.json';out.write_text(json.dumps({'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(out.relative_to(ROOT)),'rows':len(rows),'valid':sum(x['branchValid'] for x in rows)},ensure_ascii=False))
if __name__=='__main__':main()
