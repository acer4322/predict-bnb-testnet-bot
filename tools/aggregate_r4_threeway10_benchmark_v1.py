from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FILES=[P/'r4_threeway10_benchmark_chunk_0_1_v1.json',P/'r4_threeway10_benchmark_chunk_1_1_v1.json',P/'r4_threeway10_benchmark_chunk_2_2_v1.json',P/'r4_threeway10_benchmark_chunk_4_2_v1.json',P/'r4_threeway10_benchmark_chunk_6_2_v1.json',P/'r4_threeway10_benchmark_chunk_8_2_v1.json']
KEYS=['R3_R31_FULL_RESEARCH','R3_R31_MAKER10','R4_MANAGEMENT_TESTBED']
def maxdd(rows,k):
 xs=sorted(rows,key=lambda r:int(r['resolvedAtMs']));cum=0.;peak=0.;dd=0.
 for r in xs:
  cum+=float(r[k]['pnlUsdt']);peak=max(peak,cum);dd=max(dd,peak-cum)
 return dd

def agg(rows,k):
 pnl=np.asarray([float(r[k]['pnlUsdt']) for r in rows],float);floor=np.asarray([float(r[k]['finalFloor']) for r in rows],float);absn=np.asarray([float(r[k]['finalAbsNet']) for r in rows],float)
 return {'markets':len(rows),'positiveMarkets':int(np.sum(pnl>0)),'winRate':float(np.mean(pnl>0)),'totalPnlUsdt':float(pnl.sum()),'meanPnlUsdt':float(pnl.mean()),'medianPnlUsdt':float(np.median(pnl)),'maxGainUsdt':float(pnl.max()),'maxLossUsdt':float(pnl.min()),'maxDrawdownUsdt':float(maxdd(rows,k)),'meanFinalFloor':float(floor.mean()),'medianFinalFloor':float(np.median(floor)),'meanFinalAbsNet':float(absn.mean()),'medianFinalAbsNet':float(np.median(absn)),'makerFillEvents':int(sum(int(r[k]['makerFillEvents']) for r in rows)),'takerFills':int(sum(int(r[k]['takerFills']) for r in rows))}
def main():
 rows=[];events=[]
 for f in FILES:
  d=json.loads(f.read_text());rows+=d['rows'];events+=d.get('r4Events') or []
 ids=json.loads((P/'r4_threeway10_benchmark_ids_v1.json').read_text());by={int(r['marketId']):r for r in rows};rows=[by[int(i)] for i in ids]
 if len(rows)!=10 or any('error' in r for r in rows):raise RuntimeError('benchmark rows incomplete')
 if not all(bool(r.get('r4SeedEquivalent')) for r in rows):raise RuntimeError('seed equivalence failure')
 summary={k:agg(rows,k) for k in KEYS}
 paired={}
 for a,b in [('R3_R31_MAKER10','R3_R31_FULL_RESEARCH'),('R4_MANAGEMENT_TESTBED','R3_R31_FULL_RESEARCH'),('R4_MANAGEMENT_TESTBED','R3_R31_MAKER10')]:
  ds=[float(r[a]['pnlUsdt'])-float(r[b]['pnlUsdt']) for r in rows];paired[f'{a}_minus_{b}']={'meanPnlDelta':float(np.mean(ds)),'medianPnlDelta':float(np.median(ds)),'wins':int(sum(x>1e-9 for x in ds)),'losses':int(sum(x<-1e-9 for x in ds)),'ties':int(sum(abs(x)<=1e-9 for x in ds)),'totalPnlDelta':float(sum(ds))}
 m10=[r.get('maker10Audit') or {} for r in rows];r4stats=[r.get('r4MgmtStats') or {} for r in rows];
 out={'version':'R4_THREEWAY10_ACTION_ENABLED_BENCHMARK_V1','researchOnly':True,'actionAuthority':'HFT_SIM_ONLY','contract':'r4_threeway10_action_enabled_benchmark_contract_v1.json','cohort':ids,'allR4SeedEquivalent':True,'errors':0,'summary':summary,'paired':paired,'maker10Audit':{'allMarketsMakerQty10':all(bool(x.get('allMakerQty10')) for x in m10),'takerSizingModes':sorted(set(str(x.get('takerSizingMode')) for x in m10)),'marketsWithDynamicNon10Taker':int(sum(bool(x.get('takerDynamicNon10Observed')) for x in m10))},'r4ManagementActivity':{'riskCandidates':int(sum(int(x.get('riskCandidates') or 0) for x in r4stats)),'positiveFloorPreserves':int(sum(int(x.get('positiveFloorPreserves') or 0) for x in r4stats)),'pathEntries':int(sum(int(x.get('pathEntries') or 0) for x in r4stats)),'pathPreserves':int(sum(int(x.get('pathPreserves') or 0) for x in r4stats)),'crossOverrides':int(sum(int(x.get('crossOverrides') or 0) for x in r4stats))},'markets':rows,'interpretationBoundary':'Current action-enabled benchmark only. Newer Ownership Continuity / Objective Purpose / Stage3 heads remain shadow-only and were not granted action authority for this score.'}
 outp=P/'r4_threeway10_action_enabled_benchmark_v1.json';outp.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({k:out[k] for k in ['allR4SeedEquivalent','summary','paired','maker10Audit','r4ManagementActivity']},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
