from __future__ import annotations
import json,math
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[1]
C8=ROOT/'data/research/lan_worker_returns/r4-winrate-conversion-recent8-v2/conversion.json'
T8=ROOT/'data/research/lan_worker_returns/r4-winrate-recent8-causal-v1/teacher.json'
C16=[ROOT/'data/research/lan_worker_returns/r4-winrate-recent16-a-v1/conversion.json',ROOT/'data/research/lan_worker_returns/r4-winrate-recent16-b-v1/conversion.json']
P16=[ROOT/'data/research/lan_worker_returns/r4-winrate-recent16-path-a-v1/path.json',ROOT/'data/research/lan_worker_returns/r4-winrate-recent16-path-b-v1/path.json']
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_adaptive_winrate_recent24_topology_v1.json'
def topo(s):
 r=int(s.get('repairSideActiveCount') or 0);d=int(s.get('dominantSideActiveCount') or 0)
 return 'EMPTY' if r==0 and d==0 else 'REPAIR_ONLY' if r>0 and d==0 else 'DOMINANT_ONLY' if d>0 and r==0 else 'DUAL'
def main():
 rows=[]
 c8={int(r['marketId']):r for r in json.loads(C8.read_text(encoding='utf-8'))['rows']};t8={int(r['marketId']):r for r in json.loads(T8.read_text(encoding='utf-8'))['rows']}
 for mid,c in c8.items():
  tr=t8[mid];forced=((tr.get('counterfactual') or {}).get('forced') or []);s=((forced[0].get('activeOrderPathState') or {}).get('summary') or {}) if forced else {}
  rows.append({'marketId':mid,'cohort':'RECENT8','conversion':c['conversion'],'deltaPnl':c.get('deltaPnl'),'topology':topo(s),'activeOrderCount':s.get('activeOrderCount')})
 conv={};path={}
 for fp in C16:
  for r in json.loads(fp.read_text(encoding='utf-8'))['rows']:conv[int(r['marketId'])]=r
 for fp in P16:
  for r in json.loads(fp.read_text(encoding='utf-8'))['rows']:path[int(r['marketId'])]=r
 for mid,c in conv.items():
  if mid not in path: rows.append({'marketId':mid,'cohort':'RECENT16','conversion':c['conversion'],'deltaPnl':c.get('deltaPnl'),'topology':'NO_CANDIDATE','activeOrderCount':None});continue
  s=((path[mid].get('pathState') or {}).get('summary') or {});rows.append({'marketId':mid,'cohort':'RECENT16','conversion':c['conversion'],'deltaPnl':c.get('deltaPnl'),'topology':topo(s),'activeOrderCount':s.get('activeOrderCount')})
 groups=defaultdict(list)
 for r in rows:groups[r['topology']].append(r)
 def summ(g):
  n=len(g); conv=Counter(x['conversion'] for x in g); return {'n':n,'conversionCounts':dict(conv),'lossToWinRateAmongBaselineLoss':sum(x['conversion']=='LOSS->WIN' for x in g)/max(1,sum(str(x['conversion']).startswith('LOSS->') and x['conversion']!='LOSS->NO_CANDIDATE' for x in g)),'winnerDamageRate':sum(x['conversion']=='WIN->LOSS' for x in g)/max(1,sum(str(x['conversion']).startswith('WIN->') for x in g))}
 rep={'version':'R4_ADAPTIVE_WINRATE_RECENT24_TOPOLOGY_V1','researchOnly':True,'actionAuthority':False,'marketCount':len(rows),'conversionCounts':dict(Counter(r['conversion'] for r in rows)),'topology':{k:summ(v) for k,v in groups.items()},'rows':rows,'decision':'TOPOLOGY_DIAGNOSTIC_ONLY'}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'conversionCounts':rep['conversionCounts'],'topology':rep['topology']},indent=2))
if __name__=='__main__':main()
