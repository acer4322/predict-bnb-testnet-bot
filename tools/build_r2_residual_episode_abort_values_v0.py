from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_residual_episode_abort_oracle_v0 import run_recovery
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 rows=[]
 for i,mid in enumerate(mids,1):
  r=run_recovery(mid,enable_intervention=False,candidate_delay_ms=0,passive_priority=False,abort_after_candidate=True)
  row={'marketId':mid,'abortPnl':r['realizedPnl'],'candidateAtMs':r.get('candidateAtMs'),'candidateRecoverySide':r.get('candidateRecoverySide')}
  rows.append(row);print(json.dumps({'progress':i,**row}))
 out=OUT/a.output;out.write_text(json.dumps({'version':'R2_RESIDUAL_EPISODE_ABORT_VALUES_V0','rows':rows},indent=2),encoding='utf-8');print(json.dumps({'done':str(out),'rows':len(rows)}))
if __name__=='__main__':main()
