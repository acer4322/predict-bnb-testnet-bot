from __future__ import annotations
import json,statistics,math
from pathlib import Path
SOURCES={
 'TEST20':'data/research/lan_worker_returns/dagger60-lp-test20-extreme-trace-20260909-v1/trace.json',
 'FRESH101':'data/research/lan_worker_returns/dagger60-lp-fresh101-extreme-trace-20260909-v1/trace.json',
 'EXTERNAL24':'data/research/lan_worker_returns/dagger60-lp-external24-trace-20260909-v1/trace.json'}
OUT=Path('data/research/r4_v0/p0_provenance_v1/DAGGER60_LOCAL_PENDING_FRESH101_RETEST_EXTREME_PACKET_V1_20260909')

def mean(xs):return sum(xs)/len(xs) if xs else None
def med(xs):return statistics.median(xs) if xs else None
def pf(rows):
 w=sum(r['pnl'] for r in rows if r['pnl']>0);l=-sum(r['pnl'] for r in rows if r['pnl']<0);return w/l if l>0 else None
def mdd(rows):
 eq=0;peak=0;mx=0
 for r in rows:eq+=r['pnl'];peak=max(peak,eq);mx=max(mx,peak-eq)
 return mx

def cohort(name,p):
 d=json.load(open(p,encoding='utf-8')); per=[]
 for i,m in enumerate(d['markets'],1):
  t=m['terminal'];dom='UP' if t['up']>t['down']+1e-9 else 'DOWN' if t['down']>t['up']+1e-9 else None;match=(dom==t['winner']) if dom else None
  acc=[a for a in m['actionAttempts'] if a.get('accepted')];mat=m['materializedActions'];eps=m['reexpandEpisodes'];pre=[e.get('reexpandPreState') for e in eps if e.get('reexpandPreState')]
  def fv(a,k):
   z=a.get('strictPastFeature') if a else None;return None if not z else z.get('featureMap',{}).get(k)
  per.append({'marketId':t['marketId'],'ordinal':i,'pnl':t['pnl'],'oppositePnl':t.get('oppositePnl'),'floor':t['floor'],'pairCoverage':t['pairCoverage'],'absNet':t['absNet'],'fills':t['fills'],'submits':t['submits'],'buyNotional':t['buyNotional'],'winner':t['winner'],'dominant':dom,'match':match,'accR':sum(a['roleAtAttempt']=='REPAIR' for a in acc),'accE':sum(a['roleAtAttempt']=='EXPAND' for a in acc),'matR':sum(a['roleAtAttempt']=='REPAIR' for a in mat),'matE':sum(a['roleAtAttempt']=='EXPAND' for a in mat),'eps':len(eps),'zero':sum(e['materializedRepairCount']==0 for e in eps),'has':sum(e['materializedRepairCount']>0 for e in eps),'prePair':[fv(x,'pair_coverage') for x in pre if fv(x,'pair_coverage') is not None],'preFloor':[fv(x,'floor') for x in pre if fv(x,'floor') is not None],'preAbs':[fv(x,'abs_net') for x in pre if fv(x,'abs_net') is not None]})
 def group(xs):
  if not xs:return {'n':0}
  pnl=sum(r['pnl'] for r in xs);buy=sum(r['buyNotional'] for r in xs);eps=sum(r['eps'] for r in xs); ar=sum(r['accR'] for r in xs);ae=sum(r['accE'] for r in xs)
  return {'n':len(xs),'pnl':pnl,'roi':pnl/buy if buy else None,'winRate':sum(r['pnl']>0 for r in xs)/len(xs),'profitFactor':pf(xs),'meanPnl':pnl/len(xs),'meanPairCoverage':mean([r['pairCoverage'] for r in xs]),'positiveFloorRate':sum(r['floor']>=0 for r in xs)/len(xs),'meanAbsNet':mean([r['absNet'] for r in xs]),'meanFills':mean([r['fills'] for r in xs]),'meanSubmits':mean([r['submits'] for r in xs]),'repairAccepted':ar,'repairMaterialized':sum(r['matR'] for r in xs),'repairMaterializationRate':sum(r['matR'] for r in xs)/ar if ar else None,'expandAccepted':ae,'expandMaterialized':sum(r['matE'] for r in xs),'expandMaterializationRate':sum(r['matE'] for r in xs)/ae if ae else None,'reexpandEpisodes':eps,'zeroRepairReexpandShare':sum(r['zero'] for r in xs)/eps if eps else None,'meanReexpandPrePairCoverage':mean([v for r in xs for v in r['prePair']]),'meanReexpandPreFloor':mean([v for r in xs for v in r['preFloor']]),'meanReexpandPreAbsNet':mean([v for r in xs for v in r['preAbs']])}
 allg=group(per);allg['maxWin']=max(r['pnl'] for r in per);allg['maxLoss']=min(r['pnl'] for r in per);allg['leaveOneBestOutPnl']=sum(r['pnl'] for r in per)-max(r['pnl'] for r in per);allg['maxSequentialDrawdown']=mdd(per);allg['dominantMatchRate']=sum(r['match'] is True for r in per)/len(per)
 return {'source':p,'overall':allg,'match':group([r for r in per if r['match'] is True]),'mismatch':group([r for r in per if r['match'] is False]),'markets':per}

out={'version':'DAGGER60_LOCAL_PENDING_THREE_COHORT_COMPARISON_V1','cohorts':{k:cohort(k,v) for k,v in SOURCES.items()}}
OUT.mkdir(parents=True,exist_ok=True);(OUT/'COHORT_COMPARISON_COMPACT.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
# concise md
c=out['cohorts'];lines=['# DAgger60 Local Pending — Three-Cohort Compact Comparison','','| Metric | TEST20 | Fresh101 | External24 |','|---|---:|---:|---:|']
for label,key in [('Markets','n'),('PnL','pnl'),('ROI','roi'),('Win rate','winRate'),('Profit factor','profitFactor'),('LOBO','leaveOneBestOutPnl'),('MDD','maxSequentialDrawdown'),('Dominant match','dominantMatchRate'),('Pair coverage','meanPairCoverage'),('Positive floor','positiveFloorRate'),('Abs-net','meanAbsNet'),('Repair materialization','repairMaterializationRate'),('Zero-repair re-expand share','zeroRepairReexpandShare')]:
 vals=[]
 for k in ['TEST20','FRESH101','EXTERNAL24']:
  v=c[k]['overall'].get(key); vals.append('NA' if v is None else f'{v:.6f}' if isinstance(v,float) else str(v))
 lines.append(f'| {label} | {vals[0]} | {vals[1]} | {vals[2]} |')
lines+=['','## Match / mismatch decomposition','','| Cohort | Match n | Match PnL | Mismatch n | Mismatch PnL | Mismatch pair cov | Mismatch positive floor |','|---|---:|---:|---:|---:|---:|---:|']
for k in ['TEST20','FRESH101','EXTERNAL24']:
 a=c[k]['match'];b=c[k]['mismatch'];lines.append(f"| {k} | {a['n']} | {a.get('pnl',0):.4f} | {b['n']} | {b.get('pnl',0):.4f} | {b.get('meanPairCoverage',0):.4f} | {b.get('positiveFloorRate',0):.4f} |")
(OUT/'COHORT_COMPARISON.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'ok':True,'test20':c['TEST20']['overall'],'fresh101':c['FRESH101']['overall'],'external24':c['EXTERNAL24']['overall']},indent=2))
