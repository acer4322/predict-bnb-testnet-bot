from __future__ import annotations
import argparse,json,sys,warnings
warnings.filterwarnings('ignore',message='X does not have valid feature names')
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_bounded_one_parent_guard_v1 as cand
from tools.run_r4_threeway10_benchmark_chunk_v1 import settlement,score
P=ROOT/'data/research/r4_v0/p0_provenance_v1'; IDS=P/'r4_threeway10_benchmark_ids_v1.json'
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,default=0);ap.add_argument('--count',type=int,default=1);a=ap.parse_args();ids=[int(x) for x in json.loads(IDS.read_text())][a.start:a.start+a.count];rows=[];events=[]
 for mid in ids:
  try:
   rep=cand.run_overlay(mid,True,events);w,_=settlement(mid);s=score(rep,w);rows.append({'marketId':mid,'score':s,'mgmtStats':rep.get('mgmtStats') or {}});print(json.dumps({'marketId':mid,'pnl':s['pnlUsdt'],'stats':rep.get('mgmtStats') or {}},ensure_ascii=False),flush=True)
  except Exception as ex:rows.append({'marketId':mid,'error':f'{type(ex).__name__}:{ex}'});print(json.dumps(rows[-1]),flush=True)
 out=P/f'r4_bounded_one_parent_threeway10_chunk_{a.start}_{len(ids)}_v1.json';out.write_text(json.dumps({'version':'R4_BOUNDED_ONE_PARENT_THREEWAY10_DEVELOPMENT_CHUNK_V1','rows':rows,'events':events},indent=2,ensure_ascii=False,allow_nan=True));print(out)
if __name__=='__main__':main()
