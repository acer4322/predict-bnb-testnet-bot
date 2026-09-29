from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_transition_incremental_hft_v1_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_management_repair_stall_risk_v1.json'
FAIL=['weakFillFailure5s','floorFailure5s','absNetFailure5s']

def score(y,p):
 y=np.asarray(y,float);p=np.asarray(p,float);q=np.isfinite(y)&np.isfinite(p);y=y[q].astype(int);p=np.clip(p[q],1e-7,1-1e-7);return {'n':len(y),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(y) and y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None}
def qlift(y,p):
 y=np.asarray(y,float);p=np.asarray(p,float);q=np.isfinite(y)&np.isfinite(p);y=y[q].astype(int);p=p[q];a,b=np.quantile(p,[.25,.75]);lo=y[p<=a];hi=y[p>=b];return {'bottomRate':float(lo.mean()),'topRate':float(hi.mean()),'topMinusBottom':float(hi.mean()-lo.mean())}
def main():
 d=pd.read_csv(SRC).dropna(subset=FAIL).copy();d['repairStall5s']=(d[FAIL].sum(axis=1)==3).astype(int);d['repairBad2of3_5s']=(d[FAIL].sum(axis=1)>=2).astype(int)
 # Existing M0 continuation beliefs become risk when inverted.
 for c in ['p_m0_port','p_m0_full','p_phase_routed','p_port','p_full']:
  if c in d:d['risk_'+c]=1-d[c].astype(float)
 signals=[c for c in ['risk_p_m0_port','risk_p_m0_full','risk_p_phase_routed','multi_risk_ebm','multi_risk_lgb','multi_risk_anchor','p_transition_ebm','p_transition_lgb','binary_risk_anchor','fused_anchor_50'] if c in d]
 targets=['repairStall5s','repairBad2of3_5s'];out={'version':'R4_MANAGEMENT_REPAIR_STALL_RISK_V1','researchOnly':True,'actionAuthority':False,'definition':{'repairStall5s':'all three fail in next 5s: no weak Maker fill, no floor improvement, no absNet reduction','repairBad2of3_5s':'at least two of those three fail'},'coverage':{'rows':len(d),'markets':int(d.marketId.nunique()),'stallRate':float(d.repairStall5s.mean()),'bad2of3Rate':float(d.repairBad2of3_5s.mean())},'targets':{},'phase':{},'source':{}}
 for t in targets:
  y=d[t].to_numpy();out['targets'][t]={s:{**score(y,d[s]),'quartileLift':qlift(y,d[s])} for s in signals}
 for lo,hi in [(60,120),(120,180),(180,240),(240,301)]:
  g=d[(d.seconds_left>=lo)&(d.seconds_left<hi)];key=f'{lo}-{hi}';out['phase'][key]={'rows':len(g),'markets':int(g.marketId.nunique()),'stallRate':float(g.repairStall5s.mean()),'signals':{}}
  y=g.repairStall5s.to_numpy()
  if len(np.unique(y))>1:
   for s in signals:out['phase'][key]['signals'][s]=score(y,g[s])
 for src,g in d.groupby('source'):
  so={'rows':len(g),'markets':int(g.marketId.nunique()),'stallRate':float(g.repairStall5s.mean()),'signals':{}};y=g.repairStall5s.to_numpy()
  if len(np.unique(y))>1:
   for s in signals:so['signals'][s]=score(y,g[s])
  out['source'][src]=so
 # rank by pooled stall AUC; this is diagnostic only, not a selection threshold.
 out['pooledRanking']=sorted([{'signal':s,'auc':out['targets']['repairStall5s'][s]['auc'],'ap':out['targets']['repairStall5s'][s]['ap'],'quartileLift':out['targets']['repairStall5s'][s]['quartileLift']['topMinusBottom']} for s in signals],key=lambda x:x['auc'],reverse=True)
 out['guards']=['Future 5s outcomes define scoring labels only.','No target threshold or model weight tuning.','Source/phase splits are diagnostic.','Research only; no runtime modification.'];OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
