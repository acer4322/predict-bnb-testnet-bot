from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
sys.path.insert(0,str(ROOT)); import tools.evaluate_r4_management_simulator_v61_formal20_v1 as e
DEV=e.load(e.DEV); VR=json.loads(e.VAL.read_text(encoding='utf-8')); VAL=VR['rows']; X=np.vstack([e.vec(r) for r in DEV]); XV=np.vstack([e.vec(r) for r in VAL]);q10=np.quantile(X,.1,axis=0);q90=np.quantile(X,.9,axis=0);sc=np.maximum(1e-6,q90-q10);XN=X/sc;XVN=XV/sc
out={}
for h in e.H:
 yd=np.array([e.labels(r,h) for r in DEV]); yv=np.array([e.labels(r,h) for r in VAL]); per=[]; mismatch=[]
 for j,r in enumerate(VAL):
  # Oracle knows only the actual binary WEAK/DOM stream occurrence, not event times, sizes, prices, lifecycle or economic outcome.
  target=yv[j];cand=[i for i,z in enumerate(yd) if np.array_equal(z,target)] or list(range(len(DEV)));best=min(cand,key=lambda i:float(np.sum((XVN[j]-XN[i])**2)));des=[]
  for x in e.evs(DEV[best],h):
   ee=dict(x);rel=ee.get('sideRelation')
   if rel=='WEAK':ee['side']=r['weakSide']
   elif rel=='DOMINANT':ee['side']='DOWN' if r['weakSide']=='UP' else 'UP'
   des.append(ee)
  p=e.apply(r,des,h); a=e.apply(r,e.evs(r,h),h); b={'floor':float(r['floor']),'absNet':float(r['absNet']),'coverage':float(r['coverage'])};per.append((p,a,b))
  if e.sign(p['floor']-b['floor'])!=e.sign(a['floor']-b['floor']) or e.sign(p['absNet']-b['absNet'])!=e.sign(a['absNet']-b['absNet']):
   ae=e.evs(r,h); mismatch.append({'marketId':r['marketId'],'responsibilityId':r['responsibilityId'],'streamLabel':target.tolist(),'actualEventCount':len(ae),'weakEvents':sum(x.get('sideRelation')=='WEAK' and x['eventType'] in {'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'} for x in ae),'domEvents':sum(x.get('sideRelation')=='DOMINANT' and x['eventType'] in {'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'} for x in ae),'newRoots':sum(x['eventType']=='RESPONSIBILITY_OPENED' for x in ae),'cancels':sum(x['eventType']=='CANCEL_REQUESTED' for x in ae),'completes':sum(x['eventType']=='RESPONSIBILITY_COMPLETED' for x in ae),'actualFloorDelta':a['floor']-b['floor'],'predFloorDelta':p['floor']-b['floor'],'actualAbsNetDelta':a['absNet']-b['absNet'],'predAbsNetDelta':p['absNet']-b['absNet']})
 fd=float(np.mean([e.sign(p['floor']-b['floor'])==e.sign(a['floor']-b['floor']) for p,a,b in per]));ad=float(np.mean([e.sign(p['absNet']-b['absNet'])==e.sign(a['absNet']-b['absNet']) for p,a,b in per]));fn=float(np.mean([abs(p['floor']-a['floor'])/max(1.,abs(a['floor']-b['floor']),abs(b['floor'])) for p,a,b in per]));an=float(np.mean([abs(p['absNet']-a['absNet'])/max(1.,abs(a['absNet']-b['absNet']),abs(b['absNet'])) for p,a,b in per]));cn=float(np.mean([abs(p['coverage']-a['coverage'])/max(.05,abs(a['coverage']-b['coverage']),abs(b['coverage'])) for p,a,b in per]));out[str(h)]={'oracleBinaryStreamFloorDirectionAgreement':fd,'oracleBinaryStreamAbsNetDirectionAgreement':ad,'floorNMAE':fn,'absNetNMAE':an,'coverageNMAE':cn,'mismatchRows':len(mismatch),'mismatchEventMeans':{k:float(np.mean([x[k] for x in mismatch])) if mismatch else 0 for k in ['actualEventCount','weakEvents','domEvents','newRoots','cancels','completes']}}
rep={'version':'R4_MANAGEMENT_V6_1_ORACLE_STREAM_TRAJECTORY_DIAGNOSTIC_V1','researchOnly':True,'promotionEvidence':False,'validationMarkets':20,'validationRoots':len(VAL),'question':'If actual WEAK/DOM binary stream occurrence were known, would V6 nearest empirical trajectory reconstruct portfolio direction?','horizons':out,'interpretation':'If oracle binary stream still misses direction, missing information is within-stream event topology/timing/quantity or concurrent lifecycle coupling, not primarily stream classification.'};(P/'r4_management_v61_oracle_stream_trajectory_diagnostic_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
