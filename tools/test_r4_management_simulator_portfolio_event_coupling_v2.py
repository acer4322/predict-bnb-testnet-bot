from __future__ import annotations
import json,math
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.linear_model import LogisticRegression,Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
ROOT=Path(__file__).resolve().parents[1];RET=ROOT/'data/research/lan_worker_returns';P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEVFILES=[RET/'r4-portfolio-events-fresh21/fresh21.json',RET/'r4-portfolio-events-unseen23/unseen23.json',RET/'r4-portfolio-events-next20-a/a.json',RET/'r4-portfolio-events-next20-b/b.json'];VALFILES=[RET/'r4-portfolio-events-second20-a/a.json',RET/'r4-portfolio-events-second20-b/b.json'];OUT=P/'r4_management_simulator_portfolio_event_coupling_v2.json'
FEATURES=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount'];FAMS=['NEW_WEAK_ROOT','NEW_DOM_ROOT','WEAK_FILL','DOM_FILL','WEAK_TAKER','DOM_TAKER','CANCEL','COMPLETE'];H=[5,15,30];EPS=1e-9

def load(ps):
 out=[];meta=[]
 for p in ps:
  d=json.loads(p.read_text(encoding='utf-8'));out+=d['rows'];meta.append(d)
 return out,meta
def xmat(rows):return np.asarray([[float(r.get(f) or 0) for f in FEATURES] for r in rows],dtype=float)
def fam(e):
 et=str(e.get('eventType') or '');rel=str(e.get('sideRelation') or '')
 if et=='RESPONSIBILITY_OPENED':return 'NEW_WEAK_ROOT' if rel=='WEAK' else 'NEW_DOM_ROOT' if rel=='DOMINANT' else None
 if et in {'PARTIAL_FILL','FULL_FILL'}:return 'WEAK_FILL' if rel=='WEAK' else 'DOM_FILL' if rel=='DOMINANT' else None
 if et=='TAKER_EXECUTION':return 'WEAK_TAKER' if rel=='WEAK' else 'DOM_TAKER' if rel=='DOMINANT' else None
 if et=='CANCEL_REQUESTED':return 'CANCEL'
 if et=='RESPONSIBILITY_COMPLETED':return 'COMPLETE'
 return None
def targets(r,h,f):
 es=[e for e in r.get('portfolioEvents30s') or [] if int(e.get('dtMs') or 0)<=h*1000 and fam(e)==f];occ=int(bool(es));cnt=len(es);qty=sum(float(e.get('qty') or 0) for e in es);qes=[e for e in es if float(e.get('qty') or 0)>EPS and float(e.get('price') or 0)>EPS];px=(sum(float(e['qty'])*float(e['price']) for e in qes)/sum(float(e['qty']) for e in qes)) if qes else 0.;tm=(sum(int(e.get('dtMs') or 0) for e in es)/len(es)) if es else 0.;return occ,cnt,qty,px,tm
def infer(r):
 d=max(0.,float(r.get('absNet') or 0));c=min(.999999,max(0.,float(r.get('coverage') or 0)));m=(c*d)/(2*(1-c)) if d>EPS and c>EPS else (0.0 if d>EPS else max(0.,-float(r.get('floor') or 0)));weak=str(r.get('weakSide') or 'UP');dom=str(r.get('dominantSide') or ('DOWN' if weak=='UP' else 'UP'));up=m if weak=='UP' else m+d;dn=m if weak=='DOWN' else m+d;cost=m-float(r.get('floor') or 0);return {'UP':up,'DOWN':dn,'cost':cost,'weak':weak,'dom':dom}
def geom(s):
 up=s['UP'];dn=s['DOWN'];gross=up+dn;return min(up,dn)-s['cost'],abs(up-dn),(2*min(up,dn)/gross if gross>EPS else 0.)
def apply_agg(r,preds,h):
 s=infer(r)
 for f in ['WEAK_FILL','DOM_FILL','WEAK_TAKER','DOM_TAKER']:
  z=preds[(h,f)];q=max(0.,z['expectedQty']);px=min(.99,max(.01,z['price'])) if q>EPS else 0.;side=s['weak'] if f.startswith('WEAK') else s['dom'];s[side]+=q;s['cost']+=q*px*(1.02 if 'TAKER' in f else 1.0)
 return geom(s)
def actual_geom(r,h):
 s=infer(r)
 for e in r.get('portfolioEvents30s') or []:
  if int(e.get('dtMs') or 0)>h*1000:break
  f=fam(e)
  if f not in {'WEAK_FILL','DOM_FILL','WEAK_TAKER','DOM_TAKER'}:continue
  q=float(e.get('qty') or 0);px=float(e.get('price') or 0);side=s['weak'] if f.startswith('WEAK') else s['dom'];s[side]+=q;s['cost']+=q*px*(1.02 if 'TAKER' in f else 1.)
 return geom(s)
def sign(v):return 1 if v>1e-9 else -1 if v<-1e-9 else 0
def main():
 dev,_=load(DEVFILES);val,vm=load(VALFILES);Xd=xmat(dev);Xv=xmat(val);models={};predrows=[{} for _ in val];family_rate_err=[]
 for h in H:
  for f in FAMS:
   y=[];cnt=[];qty=[];px=[];tm=[]
   for r in dev:
    a,b,c,d,e=targets(r,h,f);y.append(a);cnt.append(b);qty.append(c);px.append(d);tm.append(e)
   y=np.asarray(y);base=float(y.mean())
   if y.min()==y.max():prob=np.full(len(val),base)
   else:
    clf=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=1000));clf.fit(Xd,y);prob=clf.predict_proba(Xv)[:,1]
   pos=np.where(y>0)[0]
   def cond_pred(vals,default,log=False):
    if len(pos)<8:return np.full(len(val),default)
    yy=np.asarray(vals)[pos]
    if log:yy=np.log1p(np.maximum(0.,yy))
    reg=make_pipeline(StandardScaler(),Ridge(alpha=10.0));reg.fit(Xd[pos],yy);p=reg.predict(Xv);return np.expm1(p) if log else p
   posqty=[qty[i] for i in pos];pospx=[px[i] for i in pos if px[i]>0];postm=[tm[i] for i in pos];qdef=float(np.mean(posqty)) if posqty else 0.;pdef=float(np.mean(pospx)) if pospx else .5;tdef=float(np.mean(postm)) if postm else h*500
   qpred=cond_pred(qty,qdef,log=True);ppred=cond_pred(px,pdef,log=False);tpred=cond_pred(tm,tdef,log=False)
   actual_rate=sum(targets(r,h,f)[0] for r in val)/len(val);pred_rate=float(np.mean(prob));family_rate_err.append(abs(actual_rate-pred_rate))
   for i in range(len(val)):predrows[i][(h,f)]={'p':float(prob[i]),'expectedQty':float(max(0.,prob[i]*qpred[i])),'price':float(ppred[i]),'timeMs':float(max(0.,tpred[i]))}
 horizons={};nmae=[];fd=[];ad=[]
 for h in H:
  rec=[]
  for i,r in enumerate(val):
   base=(float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0));act=actual_geom(r,h);pred=apply_agg(r,predrows[i],h);rec.append((base,act,pred))
  def nm(j,den):
   aa=[x[1][j]-x[0][j] for x in rec];pp=[x[2][j]-x[0][j] for x in rec];return sum(abs(a-b) for a,b in zip(aa,pp))/len(aa)/max(den,sum(abs(a) for a in aa)/len(aa))
  fn=nm(0,1.);an=nm(1,1.);cn=nm(2,.05);fda=sum(sign(x[1][0]-x[0][0])==sign(x[2][0]-x[0][0]) for x in rec)/len(rec);ada=sum(sign(x[1][1]-x[0][1])==sign(x[2][1]-x[0][1]) for x in rec)/len(rec);horizons[str(h)]={'roots':len(rec),'floorNMAE':fn,'absNetNMAE':an,'coverageNMAE':cn,'floorDirectionAgreement':fda,'absNetDirectionAgreement':ada};nmae += [fn,an,cn];fd.append(fda);ad.append(ada)
 markets=set(int(r['marketId']) for r in val);exact=all(x['executionCoreExact'] for d in vm for x in d['markets']);dup=sum(int(d.get('duplicateExecutionCredit') or 0) for d in vm);meanrate=float(np.mean(family_rate_err));meanN=float(np.mean(nmae));mf=float(np.mean(fd));ma=float(np.mean(ad));checks={'validationMarkets':len(markets)>=18,'validationRoots':len(val)>=20,'executionCoreExactAll':exact,'duplicateExecutionCredit':dup==0,'meanFamilyRateAbsError':meanrate<=.12,'floorDirectionAgreement':mf>=.65,'absNetDirectionAgreement':ma>=.65,'meanPortfolioNMAE':meanN<=.35};rep={'version':'R4_MANAGEMENT_SIMULATOR_PORTFOLIO_EVENT_COUPLING_V2','researchOnly':True,'developmentRoots':len(dev),'validationMarkets':len(markets),'validationRoots':len(val),'meanFamilyRateAbsError':meanrate,'horizons':horizons,'summary':{'meanPortfolioNMAE':meanN,'meanFloorDirectionAgreement':mf,'meanAbsNetDirectionAgreement':ma,'checks':checks,'gatePass':all(checks.values())},'structural':{'executionCoreExactAll':exact,'duplicateExecutionCredit':dup},'interpretation':'Pre-registered factorized portfolio event-family occurrence + conditional quantity/price model. Economics recomputed deterministically; no validation fitting.'};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
