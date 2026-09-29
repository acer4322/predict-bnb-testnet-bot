from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.run_r4_threeway10_benchmark_chunk_v1 import settlement,score
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['FIRST_EXTRA_ONLY','DEFER_UNTIL_GAP54','NO_EXTRA_MANAGEMENT'],required=True);a=ap.parse_args()
 if a.mode=='FIRST_EXTRA_ONLY':from tools import diag_r4_1782959_first_extra_only_v1 as m
 elif a.mode=='DEFER_UNTIL_GAP54':from tools import diag_r4_1782959_defer_gap54_v1 as m
 else:from tools import diag_r4_1782959_no_extra_v1 as m
 events=[];rep=m.run_overlay(1782959,True,events);w,_=settlement(1782959);s=score(rep,w)
 out={'version':'R4_1782959_REMAINING_OVERRIDE_CAUSAL_ABLATION_V1','mode':a.mode,'marketId':1782959,'score':s,'mgmtStats':rep.get('mgmtStats') or {},'events':events}
 p=P/f'r4_1782959_causal_ablation_{a.mode.lower()}_v1.json';p.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True));print(json.dumps({'mode':a.mode,'pnl':s['pnlUsdt'],'floor':s['finalFloor'],'absNet':s['finalAbsNet'],'mgmtStats':out['mgmtStats']},ensure_ascii=False))
if __name__=='__main__':main()
