from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_pair_completion_counterfactual_v2 as cf
OUT_DEFAULT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/pair_completion_economics_v3.jsonl'
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',default=str(OUT_DEFAULT)); ap.add_argument('--reset',action='store_true'); a=ap.parse_args()
 mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; out=Path(a.output); out=out if out.is_absolute() else ROOT/out; out.parent.mkdir(parents=True,exist_ok=True)
 if a.reset and out.exists(): out.unlink()
 with out.open('a',encoding='utf-8') as fh:
  for i,m in enumerate(mids,1):
   r=cf.run_recovery(m,True); inter=r.get('intervention') or {}; f=inter.get('features') or {}
   row={'marketId':m,'checkpointMs':inter.get('atMs'),'actionMode':inter.get('actionMode'),'features':f}
   fh.write(json.dumps(row,ensure_ascii=False,allow_nan=True)+'\n'); fh.flush()
   print(json.dumps({'progress':i,'marketId':m,'lockedPairEdgePerShare':f.get('lockedPairEdgePerShare'),'marginalSurplusChunkAvgCost':f.get('marginalSurplusChunkAvgCost'),'recoveryAsk':f.get('recoveryAsk')},ensure_ascii=False),flush=True)
 print(json.dumps({'ok':True,'output':str(out),'rows':len(mids)},ensure_ascii=False))
if __name__=='__main__': main()
