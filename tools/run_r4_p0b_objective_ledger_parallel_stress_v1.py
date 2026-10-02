from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as m
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--offset',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args();pr=json.loads((P/'r4_p0b_objective_ledger_parallel_stress_preregistered_v1.json').read_text(encoding='utf-8'));ids=pr['marketIds'][a.offset:a.offset+a.count];rows=[]
 for mid in ids:
  r=m.run_market(int(mid),'ALL');rows.append(r);print(json.dumps({'marketId':mid,'pass':r['pass'],'parallelObjectiveCandidates':r.get('parallelObjectiveCandidates',0)},ensure_ascii=False),flush=True)
 out={'version':'R4_P0B_OBJECTIVE_LEDGER_PARALLEL_STRESS_CHUNK_V1','offset':a.offset,'rows':rows};p=P/f'r4_p0b_objective_ledger_parallel_stress_o{a.offset}_m{a.count}_v1.json';p.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'artifact':str(p.relative_to(ROOT)),'markets':len(rows),'passMarkets':sum(r['pass'] for r in rows),'parallelObjectiveCandidates':sum(r.get('parallelObjectiveCandidates',0) for r in rows)},ensure_ascii=False))
if __name__=='__main__':main()
