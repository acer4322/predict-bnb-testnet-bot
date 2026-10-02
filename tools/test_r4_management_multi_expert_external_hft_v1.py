from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
FILES=[
 'data/research/r4_v0/hourly/r4_management_hft_shadow_fresh24_v1_rows.csv',
 'data/research/r4_v0/hourly/r4_management_hft_shadow_unseen24_v1_rows.csv',
 'data/research/r4_v0/hourly/r4_management_hft_shadow_replication3_v1_rows.csv',
 'data/research/r4_v0/hourly/r4_management_m0_phase_routing_replication4_v1_rows.csv',
]
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_multi_expert_external_hft_v1.json'

def score(y,p):
 y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 if len(np.unique(y))<2: return {'n':len(y),'rate':float(y.mean()),'auc':None,'ap':None,'logLoss':float(log_loss(y,p,labels=[0,1]))}
 return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
 rows=[]
 for f in FILES:
  p=ROOT/f
  if not p.exists(): continue
  d=pd.read_csv(p); d['source']=p.stem; rows.append(d)
 d=pd.concat(rows,ignore_index=True)
 # dedupe exact market/time/side event so cohorts cannot silently double count
 keys=[c for c in ['marketId','t','side','kind'] if c in d.columns]
 d=d.sort_values(keys).drop_duplicates(keys,keep='last').copy()
 d=d[(d.seconds_left>=60)&(d.seconds_left<=300)&(d.build_now==1)].copy()
 # Existing frozen experts available on HFT rows: portfolio-only belief and responsibility-aware FULL belief.
 # Treat these as two management experts and test the previously frozen 65/35 collaboration idea without tuning.
 if 'p_m0_port' in d: d['p_port']=d['p_m0_port']
 if 'p_m0_full' in d: d['p_full']=d['p_m0_full']
 # replication4 already names p_port/p_full
 d['p_blend_65_35']=.65*d.p_port+.35*d.p_full
 d['p_phase_blend']=np.where(d.seconds_left<180,.65*d.p_full+.35*d.p_port,d.p_port)
 targets=['futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']
 result={'version':'R4_MANAGEMENT_MULTI_EXPERT_EXTERNAL_HFT_V1','researchOnly':True,'actionAuthority':False,
  'question':'Does fixed multi-expert collaboration preserve useful economic/path information on independent realistic-HFT management rows?',
  'coverage':{'rows':int(len(d)),'markets':int(d.marketId.nunique()),'sources':sorted(d.source.unique().tolist())},'targets':{}}
 for target in targets:
  q=d.dropna(subset=[target,'p_port','p_full']).copy(); y=q[target].astype(int)
  mets={name:score(y,q[col]) for name,col in [('PORT','p_port'),('FULL','p_full'),('BLEND_65_35','p_blend_65_35'),('PHASE_BLEND','p_phase_blend')]}
  # frozen success criterion: candidate must beat PORT on AUC and logloss, and cannot lose AP.
  for name in ['BLEND_65_35','PHASE_BLEND']:
   a=mets[name]; b=mets['PORT']; a['vsPort']={'aucDelta':a['auc']-b['auc'],'apDelta':a['ap']-b['ap'],'logLossImprovement':b['logLoss']-a['logLoss']}
   a['passesFixedRule']=bool(a['vsPort']['aucDelta']>0 and a['vsPort']['apDelta']>=0 and a['vsPort']['logLossImprovement']>0)
  result['targets'][target]=mets
 result['allTargetsPass65_35']=all(result['targets'][t]['BLEND_65_35']['passesFixedRule'] for t in targets)
 result['allTargetsPassPhaseBlend']=all(result['targets'][t]['PHASE_BLEND']['passesFixedRule'] for t in targets)
 result['guards']=['Uses completed realistic-HFT shadow rows only.','Future outcomes are scoring labels only.','Fixed 65/35 weight inherited from prior Target management experiment; no sweep.','No action authority/runtime modification.']
 OUT.write_text(json.dumps(result,indent=2),encoding='utf-8')
 print(json.dumps(result,indent=2))
if __name__=='__main__': main()
