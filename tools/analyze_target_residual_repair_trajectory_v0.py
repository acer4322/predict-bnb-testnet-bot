from __future__ import annotations

import bisect
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
GENERAL=OUT/'target_general_maker_side_hazard_v1.csv'
TAKER=OUT/'taker_event_states_v1.csv'
EX=OUT/'target_temporary_imbalance_recovery_v0_rows.csv'
REPORT=OUT/'target_residual_repair_trajectory_v0_report.json'
ROWS=OUT/'target_residual_repair_trajectory_v0_rows.csv'
CUTOFF=1787137800000
HORIZONS=[1000,3000,5000,10000,15000]

def stats(x):
 a=pd.to_numeric(pd.Series(list(x)),errors='coerce').dropna()
 return {'n':int(len(a)),'mean':float(a.mean()) if len(a) else None,'median':float(a.median()) if len(a) else None,'p25':float(a.quantile(.25)) if len(a) else None,'p75':float(a.quantile(.75)) if len(a) else None,'p90':float(a.quantile(.9)) if len(a) else None}

def main():
 g=pd.read_csv(GENERAL)
 g=g[pd.to_numeric(g.market_end_ms,errors='coerce')<CUTOFF].sort_values(['market_id','checkpoint_ms'])
 t=pd.read_csv(TAKER)
 t=t[(t.label_effect.astype(str)=='REPAIR_EFFECT') & (pd.to_numeric(t.market_end_ms,errors='coerce')<CUTOFF)].copy()
 ex=pd.read_csv(EX,usecols=['marketId','anchorFillEndMs'])
 intervals={}
 for mid,x in ex.groupby('marketId'):
  intervals[int(mid)]=[(int(v)+1,int(v)+1+15000) for v in pd.to_numeric(x.anchorFillEndMs,errors='coerce').dropna().astype('int64')]
 # market-indexed state arrays
 gi={}
 for mid,x in g.groupby('market_id'):
  x=x.sort_values('checkpoint_ms').reset_index(drop=True); gi[int(mid)]=(x,pd.to_numeric(x.checkpoint_ms,errors='coerce').astype('int64').tolist())
 rows=[]
 for e in t.sort_values(['market_id','checkpoint_ms']).itertuples(index=False):
  mid=int(e.market_id); et=int(e.checkpoint_ms)
  # residual path only: exclude events themselves occurring inside known realized overlap-excursion +15s window
  if any(a<=et<=b for a,b in intervals.get(mid,[])): continue
  if mid not in gi: continue
  x,ts=gi[mid]; i=bisect.bisect_right(ts,et)-1
  if i<0: continue
  s0=x.iloc[i]; start_abs=float(s0.maker_abs_net); start_pc=float(s0.maker_paired_coverage); start_p3=None
  r={'marketId':mid,'eventMs':et,'side':str(e.label_side),'startCheckpointMs':int(s0.checkpoint_ms),'startSecondsLeft':float(s0.seconds_left),'startMakerAbsNet':start_abs,'startMakerPairedCoverage':start_pc,'startWorstCaseFloor':float(s0.worst_case_floor),'startMakerNet':float(s0.maker_net)}
  for h in HORIZONS:
   j=bisect.bisect_left(ts,et+h)
   if j>=len(x) or int(x.iloc[j].checkpoint_ms)>et+h+1600:
    r[f'abs_{h//1000}s']=np.nan; r[f'pc_{h//1000}s']=np.nan; r[f'deltaAbs_{h//1000}s']=np.nan; continue
   z=x.iloc[j]; val=float(z.maker_abs_net); r[f'abs_{h//1000}s']=val; r[f'pc_{h//1000}s']=float(z.maker_paired_coverage); r[f'deltaAbs_{h//1000}s']=val-start_abs
  rows.append(r)
 d=pd.DataFrame(rows); d.to_csv(ROWS,index=False)
 summary={}
 for h in HORIZONS:
  k=h//1000; delta=pd.to_numeric(d[f'deltaAbs_{k}s'],errors='coerce'); valid=delta.dropna(); summary[f'{k}s']={'deltaAbs':stats(valid),'improvedRate':float((valid<-1).mean()) if len(valid) else None,'worsenedRate':float((valid>1).mean()) if len(valid) else None,'unchangedRate':float((valid.abs()<=1).mean()) if len(valid) else None,'pairedCoverage':stats(d[f'pc_{k}s'])}
 # episode spacing / bursts by market
 spacings=[]; counts=[]
 for mid,x in d.groupby('marketId'):
  arr=sorted(x.eventMs.astype('int64').tolist()); counts.append(len(arr)); spacings.extend([b-a for a,b in zip(arr,arr[1:])])
 rep={'reportVersion':'TARGET_RESIDUAL_REPAIR_TRAJECTORY_V0','researchOnly':True,'question':'After Target starts a REPAIR_EFFECT Taker outside known overlap-excursion windows, does Maker residual risk resolve immediately or persist as a multi-second repair regime?','coverage':{'events':int(len(d)),'markets':int(d.marketId.nunique()) if len(d) else 0,'cutoffMs':CUTOFF,'rows':str(ROWS)},'onset':{'makerAbsNet':stats(d.startMakerAbsNet),'makerPairedCoverage':stats(d.startMakerPairedCoverage),'secondsLeft':stats(d.startSecondsLeft),'worstCaseFloor':stats(d.startWorstCaseFloor)},'trajectory':summary,'repairEventsPerMarket':stats(counts),'interRepairSpacingMs':stats(spacings),'interpretationGuard':['REPAIR_EFFECT is an inventory-effect label, not semantic intent ground truth.','This is teacher-only descriptive analysis; no Target future state enters OUR runtime.','No outcome/PnL conditioning or threshold selection.']}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
