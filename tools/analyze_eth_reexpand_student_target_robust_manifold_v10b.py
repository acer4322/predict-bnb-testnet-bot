from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.isotonic import IsotonicRegression
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
import analyze_target_eth_btc_reexpand_economic_transfer_v9 as xfer

FEATURES=xfer.FEATURES
EPS=1e-9

class TraceSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models,iso):
        super().__init__(tape,mode,models);self.iso=iso;self.expDebt=0.;self.lastExpandDebt=0.;self.lastExpandT=None;self.lastTransitionExpand=0.;self.checkpoints=[]
    def record_fill(self,t,side,q,p):
        pre=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        super().record_fill(t,side,q,p)
        post=abs(float(self.inv['UP'])-float(self.inv['DOWN']));d=post-pre
        if d>EPS:
            self.expDebt=max(0.,self.expDebt)+d;self.lastExpandDebt=self.expDebt;self.lastExpandT=int(t);self.lastTransitionExpand=1.
        elif d<-EPS and self.expDebt>EPS:
            self.expDebt=max(0.,self.expDebt-(-d));self.lastTransitionExpand=0.
            if self.expDebt<=EPS:self.expDebt=self.lastExpandDebt=0.;self.lastExpandT=None
    def would_expand(self,side,qty):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);pre=abs(u-d)
        if side=='UP':u+=qty
        else:d+=qty
        return abs(u-d)>pre+EPS
    def authority_features(self,t):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);gross=u+d;gap=abs(u-d);paired=min(u,d);cost=float(self.cost)
        progress=max(0.,min(1.,(self.lastExpandDebt-self.expDebt)/self.lastExpandDebt)) if self.expDebt>EPS and self.lastExpandDebt>EPS else 1.
        weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
        au=float(self.sideCost['UP'])/u if u>EPS else 0.;ad=float(self.sideCost['DOWN'])/d if d>EPS else 0.;wav=au if weak=='UP' else ad if weak=='DOWN' else 0.;dav=ad if dom=='DOWN' else au if dom=='UP' else 0.
        return np.asarray([progress,self.expDebt/max(gross,1.),2*paired/gross if gross>EPS else 0.,(paired-cost)/max(cost,1.),(max(u,d)-cost)/max(cost,1.),gap/max(gross,1.),float((int(t)-int(self.lastExpandT)) if self.lastExpandT is not None else 60000),self.lastTransitionExpand,wav,dav],float)
    def run_trace(self,models,winner,market_id):
        ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in ups:
            t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
            if not qv:continue
            if self.firstValid is None:self.firstValid=t
            if (end-t)/1000.<=180:continue
            self.seed_if_needed(t,qv)
            if not self.seeded:continue
            x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
            if pa<models['actionTh']:continue
            ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
            if self.expDebt>EPS and self.would_expand(side,qty):
                af=self.authority_features(t);prob=float(self.iso.predict([af[0]])[0]);allow=prob>=.5
                self.checkpoints.append({'marketId':int(market_id),'t':int(t),'x':af.tolist(),'progressAuthority':prob,'allowed':bool(allow),'side':side,'qty':qty})
                if not allow:continue
            self.submit(t,side,p,qty)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)

def quantile_summary(a):
    a=np.asarray(a,float)
    return {'n':int(len(a)),'p05':float(np.quantile(a,.05)),'p25':float(np.quantile(a,.25)),'median':float(np.median(a)),'p75':float(np.quantile(a,.75)),'p95':float(np.quantile(a,.95))}

def robust_gap(target,student):
    q=quantile_summary(target);s=quantile_summary(student);iqr=max(q['p75']-q['p25'],1e-9)
    outside=float(np.mean((np.asarray(student)<q['p05'])|(np.asarray(student)>q['p95'])))
    return {'target':q,'student':s,'medianIqrGap':float((s['median']-q['median'])/iqr),'outsideTarget90Rate':outside}

def assoc_auc(data,asset,period):
    ends=sorted(set(r['end'] for r in data[asset] if r['end'] is not None));cut=ends[int(len(ends)*.70)]
    z=[r for r in data[asset] if r['end'] is not None and ((r['end']<cut) if period=='early' else (r['end']>=cut))]
    X=np.asarray([r['x'] for r in z],float);y=np.asarray([r['y'] for r in z],int);out=[]
    for j in range(X.shape[1]):
        try:a=float(roc_auc_score(y,X[:,j]))
        except Exception:a=.5
        out.append(a)
    return out

def perturb(X,j,kind):
    Z=X.copy();name=FEATURES[j]
    if name in ('repair_progress','paircov','gap_ratio'):
        Z[:,j]=np.clip(Z[:,j]+(.05 if kind=='up' else -.05),0,1)
    elif name in ('debt_ratio',):
        Z[:,j]=np.clip(Z[:,j]*(1.10 if kind=='up' else .90),0,None)
    elif name in ('floor_ratio','best_pnl_ratio'):
        Z[:,j]=Z[:,j]+(.05 if kind=='up' else -.05)
    elif name=='ms_since_expand':
        Z[:,j]=np.clip(Z[:,j]*(1.20 if kind=='up' else .80),0,None)
    elif name=='last_transition_expand':
        Z[:,j]=1-Z[:,j]
    elif name in ('weak_avg_cost','dom_avg_cost'):
        Z[:,j]=np.clip(Z[:,j]+(.03 if kind=='up' else -.03),0,1)
    return Z

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_robust_manifold_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj)
        eth=xfer.build(a.target_db,'ETH');btc=xfer.build(a.target_db,'BTC');data={'ETH':eth,'BTC':btc}
        Xeth=np.asarray([r['x'] for r in eth],float);yeth=np.asarray([r['y'] for r in eth],int)
        iso=IsotonicRegression(y_min=0,y_max=1,increasing=True,out_of_bounds='clip').fit(Xeth[:,0],yeth)
        auth=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=50,l2_regularization=4.,class_weight='balanced',random_state=31).fit(Xeth,yeth)
        test=[r for r in cohort if r['split']!='TRAIN40'];checks=[]
        for i,cr in enumerate(test,1):
            sim=TraceSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,iso)
            try:sim.run_trace(models,cr['winner'],cr['marketId']);checks.extend(sim.checkpoints)
            finally:sim.close()
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'checkpoints':len(checks)}),flush=True)
        Xs=np.asarray([r['x'] for r in checks],float) if checks else np.empty((0,len(FEATURES)))
        Xt=np.asarray([r['x'] for r in eth if r['y']==1],float)
        gaps={FEATURES[j]:robust_gap(Xt[:,j],Xs[:,j]) for j in range(len(FEATURES))} if len(Xs) else {}
        Xa=np.asarray([r['x'] for r in checks if r['allowed']],float) if any(r['allowed'] for r in checks) else np.empty((0,len(FEATURES)))
        Xb=np.asarray([r['x'] for r in checks if not r['allowed']],float) if any(not r['allowed'] for r in checks) else np.empty((0,len(FEATURES)))
        allowed_gaps={FEATURES[j]:robust_gap(Xt[:,j],Xa[:,j]) for j in range(len(FEATURES))} if len(Xa) else {}
        blocked_gaps={FEATURES[j]:robust_gap(Xt[:,j],Xb[:,j]) for j in range(len(FEATURES))} if len(Xb) else {}
        aucs={asset:{period:assoc_auc(data,asset,period) for period in ('early','late')} for asset in ('BTC','ETH')}
        invariants=[];asset_specific=[]
        for j,nm in enumerate(FEATURES):
            vals=[aucs[a][p][j] for a in ('BTC','ETH') for p in ('early','late')];sg=[1 if v>=.52 else -1 if v<=.48 else 0 for v in vals]
            item={'feature':nm,'aucs':{'BTCearly':vals[0],'BTClate':vals[1],'ETHearly':vals[2],'ETHlate':vals[3]}}
            if all(x==sg[0] and x!=0 for x in sg):invariants.append(item)
            elif (sg[2]==sg[3] and sg[2]!=0) and (sg[0]!=sg[2] or sg[1]!=sg[2]):asset_specific.append(item)
        sens={}
        if len(Xs):
            basep=auth.predict_proba(Xs)[:,1];base=basep>=.5
            for j,nm in enumerate(FEATURES):
                arr=[]
                for kind in ('down','up'):
                    Z=perturb(Xs,j,kind);p=auth.predict_proba(Z)[:,1];arr.append({'direction':kind,'flipRate':float(np.mean((p>=.5)!=base)),'meanAbsProbDelta':float(np.mean(np.abs(p-basep)))})
                sens[nm]=arr
        brittle=sorted([{'feature':k,'maxFlipRate':max(x['flipRate'] for x in v),'maxMeanAbsProbDelta':max(x['meanAbsProbDelta'] for x in v)} for k,v in sens.items()],key=lambda z:z['maxFlipRate'],reverse=True)
        out={'version':'ETH_REEXPAND_STUDENT_TARGET_ROBUST_MANIFOLD_V10B','researchOnly':True,'controllerMutation':False,'features':FEATURES,'studentCheckpoints':len(checks),'allowedCheckpoints':int(len(Xa)),'blockedCheckpoints':int(len(Xb)),'targetEthReexpandStates':int(len(Xt)),'manifoldGaps':gaps,'allowedManifoldGaps':allowed_gaps,'blockedManifoldGaps':blocked_gaps,'crossAssetAssociations':aucs,'crossAssetStableInvariants':invariants,'ethSpecificAssociations':asset_specific,'economicClassifierPerturbationSensitivity':sens,'brittleRanking':brittle,'boundary':['Target used only as reference distribution/association source, never copied thresholds into runtime','Student trajectory is frozen progress-authority candidate on consumed Fresh101 development cohort','Perturbations are diagnostic only: relative feature shifts, no strategy tuning or promotion','Cross-asset invariant requires same association direction in BTC/ETH and early/late chronology'],'round1Offline':off1,'round2Offline':off2}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'studentCheckpoints':len(checks),'allowedCheckpoints':int(len(Xa)),'blockedCheckpoints':int(len(Xb)),'allowedTopGaps':sorted([{'feature':k,**v} for k,v in allowed_gaps.items()],key=lambda z:abs(z['medianIqrGap']),reverse=True)[:5],'blockedTopGaps':sorted([{'feature':k,**v} for k,v in blocked_gaps.items()],key=lambda z:abs(z['medianIqrGap']),reverse=True)[:5],'invariants':invariants,'ethSpecific':asset_specific,'topBrittle':brittle[:5],'topGaps':sorted([{'feature':k,**v} for k,v in gaps.items()],key=lambda z:abs(z['medianIqrGap']),reverse=True)[:5]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
