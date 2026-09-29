from __future__ import annotations
import json,math,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';OBS=ROOT/'data/research/lan_worker_returns/r4-e2e-late20f-observed-v3/late20f_end_to_end_observed_v1.json';H=[5,15,30];EPS=1e-9

def direction(a,b):return 1 if b>a+EPS else -1 if b<a-EPS else 0
def infer(r):
 a=float(r.get('absNet') or r.get('abs_gap') or 0);ratio=float(r.get('absnet_ratio') or 0);cov=float(r.get('coverage') or 0)
 if ratio>1e-8:g=a/ratio
 elif cov<1-1e-8 and a>0:g=a/(1-cov)
 else:return None
 mn=(g-a)/2;mx=(g+a)/2;w=str(r.get('weakSide') or r.get('checkpointSide'))
 up,dn=(mn,mx) if w=='UP' else (mx,mn);cost=mn-float(r.get('floor') or 0);return [up,dn,cost]
def apply(st,side,px,q):
 up,dn,c=st
 if side=='UP':up+=q
 else:dn+=q
 c+=px*q;g=up+dn;mn=min(up,dn);return {'floor':mn-c,'absNet':abs(up-dn),'coverage':2*mn/g if g>EPS else 0}
def state_at(rows,t):
 if not rows:return None
 x=min(rows,key=lambda z:abs(int(z['t'])-t));return x if abs(int(x['t'])-t)<=750 else None
def main():
 d=json.loads(OBS.read_text());life=d['lifecycleRows'];exe=d['executionRows'];gm=defaultdict(list);first={}
 for r in life:gm[int(r['marketId'])].append(r);k=(int(r['marketId']),str(r['checkpointResponsibilityId']));first[k]=r if k not in first or int(r['t'])<int(first[k]['t']) else first[k]
 for m in gm:gm[m].sort(key=lambda x:int(x['t']))
 em={(int(x['marketId']),str(x['responsibilityId'])):x for x in exe};out={}
 for h in H:
  rows=[]
  for k,r0 in first.items():
   st=infer(r0);e=em.get(k)
   if st is None or e is None:continue
   act=state_at(gm[k[0]],int(r0['t'])+h*1000)
   if act is None:continue
   fills=[x for x in e.get('fills30s',[]) if int(x['dtMs'])<=h*1000];q=sum(float(x['qty']) for x in fills);pred=apply(st,str(r0.get('checkpointSide')),float(r0.get('requested_px') or 0),q);ini={'floor':float(r0.get('floor') or 0),'absNet':float(r0.get('absNet') or r0.get('abs_gap') or 0),'coverage':float(r0.get('coverage') or 0)};actual={'floor':float(act.get('floor') or 0),'absNet':float(act.get('absNet') or act.get('abs_gap') or 0),'coverage':float(act.get('coverage') or 0)}
   rows.append((ini,pred,actual,q))
  n=len(rows)
  def agr(k):return sum(direction(x[0][k],x[1][k])==direction(x[0][k],x[2][k]) for x in rows)/n if n else 0
  def nmae(k):return (sum(abs(x[1][k]-x[2][k]) for x in rows)/n)/max(1,sum(abs(x[2][k]-x[0][k]) for x in rows)/n) if n else math.inf
  out[str(h)]={'roots':n,'rootsWithRealizedRootFill':sum(x[3]>EPS for x in rows),'oracleRootFillFloorDirectionAgreement':agr('floor'),'oracleRootFillAbsNetDirectionAgreement':agr('absNet'),'oracleRootFillFloorNMAE':nmae('floor'),'oracleRootFillAbsNetNMAE':nmae('absNet'),'oracleRootFillCoverageNMAE':nmae('coverage')}
 # Correct lifecycle labels from explicit responsibility completion timestamps.
 lifecycle={}
 for h in H:
  vals=[]
  for e in exe:
   vals.append(1 if e.get('completedDtMs') is not None and int(e['completedDtMs'])<=h*1000 else 0)
  lifecycle[str(h)]={'roots':len(vals),'actualCompletionRate':sum(vals)/len(vals) if vals else 0}
 rep={'version':'R4_MANAGEMENT_SIMULATOR_END_TO_END_COUPLING_DIAGNOSTIC_V1','researchOnly':True,'oracleRootFillPortfolioReplay':out,'explicitLifecycleActual':lifecycle,'interpretation':'Uses realized root-local fills as an oracle but recomputes economics deterministically. If portfolio trajectory remains poor, missing market-level concurrent responsibilities/Taker/new-root/handoff events dominate and the next simulator layer must be portfolio event coupling, not a larger execution model.'};(P/'r4_management_simulator_end_to_end_coupling_diagnostic_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
