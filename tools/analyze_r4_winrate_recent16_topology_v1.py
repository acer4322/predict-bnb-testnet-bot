from __future__ import annotations
import json, math
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[1]
CF=[ROOT/'data/research/lan_worker_returns/r4-winrate-recent16-a-v1/conversion.json',ROOT/'data/research/lan_worker_returns/r4-winrate-recent16-b-v1/conversion.json']
PF=[ROOT/'data/research/lan_worker_returns/r4-winrate-recent16-path-a-v1/path.json',ROOT/'data/research/lan_worker_returns/r4-winrate-recent16-path-b-v1/path.json']
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_adaptive_winrate_recent16_topology_v1.json'
def fin(v):
 try:
  x=float(v);return x if math.isfinite(x) else None
 except:return None
def main():
 conv={}
 for fp in CF:
  for r in json.loads(fp.read_text(encoding='utf-8'))['rows']:conv[int(r['marketId'])]=r
 path={}
 for fp in PF:
  for r in json.loads(fp.read_text(encoding='utf-8'))['rows']:path[int(r['marketId'])]=r
 rows=[];groups=defaultdict(list)
 for mid,r in conv.items():
  pr=path.get(mid);s=((pr or {}).get('pathState') or {}).get('summary') or {}
  ra=int(s.get('repairSideActiveCount') or 0);da=int(s.get('dominantSideActiveCount') or 0)
  topo='EMPTY' if ra==0 and da==0 else 'REPAIR_ONLY' if ra>0 and da==0 else 'DOMINANT_ONLY' if da>0 and ra==0 else 'DUAL'
  rr={'marketId':mid,'conversion':r.get('conversion'),'baselinePnl':(r.get('baseline') or {}).get('pnlUsdt'),'counterfactualPnl':(r.get('counterfactual') or {}).get('pnlUsdt'),'deltaPnl':r.get('deltaPnl'),'topology':topo if pr else 'NO_CANDIDATE','activeOrderCount':s.get('activeOrderCount'),'repairCount':ra,'dominantCount':da,'repairQuoteOffset':fin(s.get('repairBestQuoteOffsetTicks')),'dominantQuoteOffset':fin(s.get('dominantBestQuoteOffsetTicks')),'repairAgeS':(fin(s.get('repairMeanOrderAgeMs'))/1000 if fin(s.get('repairMeanOrderAgeMs')) is not None else None),'dominantAgeS':(fin(s.get('dominantMeanOrderAgeMs'))/1000 if fin(s.get('dominantMeanOrderAgeMs')) is not None else None),'repairDepletion':fin(s.get('repairMeanDepletionRatio')),'dominantDepletion':fin(s.get('dominantMeanDepletionRatio'))}
  rows.append(rr);groups[topo if pr else 'NO_CANDIDATE'].append(rr)
 def summarize(g):
  return {'n':len(g),'conversionCounts':dict(Counter(x['conversion'] for x in g)),'meanDeltaPnl':sum((x['deltaPnl'] or 0) for x in g if x['deltaPnl'] is not None)/max(1,sum(x['deltaPnl'] is not None for x in g))}
 rep={'version':'R4_ADAPTIVE_WINRATE_RECENT16_TOPOLOGY_V1','researchOnly':True,'actionAuthority':False,'marketCount':len(rows),'conversionCounts':dict(Counter(r['conversion'] for r in rows)),'topology':{k:summarize(v) for k,v in groups.items()},'rows':rows,'interpretationBoundary':'Descriptive topology audit only. No thresholds fitted.'}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
 print(json.dumps({'conversionCounts':rep['conversionCounts'],'topology':rep['topology'],'keyRows':[r for r in rows if r['conversion'] in {'LOSS->WIN','WIN->LOSS'}]},indent=2))
if __name__=='__main__':main()
