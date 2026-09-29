from __future__ import annotations
import json,math,statistics
from pathlib import Path
from collections import Counter
import numpy as np
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';RET=ROOT/'data/research/lan_worker_returns'
DEV1=RET/'r4-portfolio-events-fresh21/fresh21.json';DEV2=RET/'r4-portfolio-events-unseen23/unseen23.json';VALA=RET/'r4-portfolio-events-next20-a/a.json';VALB=RET/'r4-portfolio-events-next20-b/b.json';OUT=P/'r4_management_simulator_portfolio_event_coupling_v1.json'
FEATURES=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount'];H=[5,15,30];K=15;EPS=1e-9

def rows(path):return json.loads(path.read_text(encoding='utf-8'))['rows']
def vec(r):return np.asarray([float(r.get(f) or 0) for f in FEATURES],dtype=float)
def infer_state(r):
 d=max(0.,float(r.get('absNet') or 0));c=min(.999999,max(0.,float(r.get('coverage') or 0)));m=(c*d)/(2*(1-c)) if d>EPS and c>EPS else (0.0 if d>EPS else max(0.,-float(r.get('floor') or 0)))
 weak=str(r.get('weakSide') or 'UP');dom=str(r.get('dominantSide') or ('DOWN' if weak=='UP' else 'UP'));up=m if weak=='UP' else m+d;dn=m if weak=='DOWN' else m+d;cost=m-float(r.get('floor') or 0)
 return {'UP':up,'DOWN':dn,'cost':cost}
def geom(s):
 up=s['UP'];dn=s['DOWN'];cost=s['cost'];gross=up+dn;floor=min(up,dn)-cost;absn=abs(up-dn);cov=(2*min(up,dn)/gross) if gross>EPS else 0.;return floor,absn,cov
def mapped_side(ev,target):
 rel=ev.get('sideRelation');weak=str(target.get('weakSide') or 'UP');dom=str(target.get('dominantSide') or ('DOWN' if weak=='UP' else 'UP'))
 if rel=='WEAK':return weak
 if rel=='DOMINANT':return dom
 return str(ev.get('side') or weak)
def apply_episode(target,events,h):
 s=infer_state(target)
 for e in events:
  if int(e.get('dtMs') or 0)>h*1000:break
  et=str(e.get('eventType') or '')
  if et not in {'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'}:continue
  q=float(e.get('qty') or 0);px=float(e.get('price') or 0);side=mapped_side(e,target)
  if q<=EPS:continue
  s[side]+=q;s['cost']+=q*px
  if et=='TAKER_EXECUTION':s['cost']+=q*px*.02
 return geom(s)
def actual_at(r,h):return apply_episode(r,r.get('portfolioEvents30s') or [],h)
def sign(x):return 1 if x>1e-9 else -1 if x<-1e-9 else 0
def main():
 dev=rows(DEV1)+rows(DEV2);val=rows(VALA)+rows(VALB);A=np.vstack([vec(r) for r in dev]);q10=np.quantile(A,.1,axis=0);q90=np.quantile(A,.9,axis=0);sc=np.maximum(1e-6,q90-q10);AN=A/sc
 per={h:[] for h in H};fallback=[]
 for r in val:
  v=vec(r);dist=np.sum((AN-v/sc)**2,axis=1);kk=min(K,len(dev));idx=np.argpartition(dist,kk-1)[:kk];fallback.append(float(np.min(dist)))
  base=(float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0))
  for h in H:
   act=actual_at(r,h);pp=[apply_episode(r,dev[int(i)].get('portfolioEvents30s') or [],h) for i in idx];pred=tuple(sum(x[j] for x in pp)/len(pp) for j in range(3));per[h].append({'base':base,'actual':act,'pred':pred})
 horizons={};all_nmae=[];fdir=[];adir=[]
 for h in H:
  xs=per[h];fd=[x['actual'][0]-x['base'][0] for x in xs];fp=[x['pred'][0]-x['base'][0] for x in xs];ad=[x['actual'][1]-x['base'][1] for x in xs];ap=[x['pred'][1]-x['base'][1] for x in xs];cd=[x['actual'][2]-x['base'][2] for x in xs];cp=[x['pred'][2]-x['base'][2] for x in xs]
  fn=sum(abs(a-b) for a,b in zip(fd,fp))/len(xs)/max(1.,sum(abs(a) for a in fd)/len(xs));an=sum(abs(a-b) for a,b in zip(ad,ap))/len(xs)/max(1.,sum(abs(a) for a in ad)/len(xs));cn=sum(abs(a-b) for a,b in zip(cd,cp))/len(xs)/max(.05,sum(abs(a) for a in cd)/len(xs));fda=sum(sign(a)==sign(b) for a,b in zip(fd,fp))/len(xs);ada=sum(sign(a)==sign(b) for a,b in zip(ad,ap))/len(xs)
  horizons[str(h)]={'roots':len(xs),'floorNMAE':fn,'absNetNMAE':an,'coverageNMAE':cn,'floorDirectionAgreement':fda,'absNetDirectionAgreement':ada,'actualEventMean':sum(len([e for e in r.get('portfolioEvents30s') or [] if int(e.get('dtMs') or 0)<=h*1000]) for r in val)/len(val)};all_nmae += [fn,an,cn];fdir.append(fda);adir.append(ada)
 ma=sum(all_nmae)/len(all_nmae);mf=sum(fdir)/len(fdir);md=sum(adir)/len(adir);struct={'validationMarkets':len(set(int(r['marketId']) for r in val)),'validationRoots':len(val),'executionCoreExactAll':all(x['executionCoreExact'] for p in (VALA,VALB) for x in json.loads(p.read_text())['markets']),'duplicateExecutionCredit':sum(json.loads(p.read_text())['duplicateExecutionCredit'] for p in (VALA,VALB))};checks={'validationMarkets':struct['validationMarkets']>=20,'validationRoots':struct['validationRoots']>=20,'executionCoreExactAll':struct['executionCoreExactAll'],'duplicateExecutionCredit':struct['duplicateExecutionCredit']==0,'floorDirectionAgreement':mf>=.65,'absNetDirectionAgreement':md>=.65,'meanPortfolioNMAE':ma<=.35};rep={'version':'R4_MANAGEMENT_SIMULATOR_PORTFOLIO_EVENT_COUPLING_V1','researchOnly':True,'developmentRoots':len(dev),'validationMarkets':struct['validationMarkets'],'validationRoots':struct['validationRoots'],'kNearestEpisodes':K,'horizons':horizons,'summary':{'meanPortfolioNMAE':ma,'meanFloorDirectionAgreement':mf,'meanAbsNetDirectionAgreement':md,'checks':checks,'gatePass':all(checks.values())},'structural':struct,'nearestDistanceMedian':statistics.median(fallback),'interpretation':'Frozen whole-portfolio event episode transfer. Event side relation is mapped into target weak/dominant geometry; quantities/prices remain event primitives; deterministic ledger recomputes economics. No validation fitting.'};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
