from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics,math
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
EPS=1e-9
SUP_FEATURES=['repair_progress','log_age_since_expand','role_margin_expand_minus_repair','action_margin']

class LifeSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models,traj=None):
        super().__init__(tape,mode,models);self.traj=sorted(traj or [],key=lambda r:r['t']);self.ti=0;self.target={'UP':0.,'DOWN':0.};self.expDebt=0.;self.peakDebt=0.;self.lastExpandT=None;self.supBlocks=0;self.supAllows=0
    def advance_target(self,t):
        while self.ti<len(self.traj) and int(self.traj[self.ti]['t'])<int(t):
            r=self.traj[self.ti];self.target[r['side']]+=float(r['shares']);self.ti+=1
    def record_fill(self,t,side,q,p):
        pre=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        super().record_fill(t,side,q,p)
        post=abs(float(self.inv['UP'])-float(self.inv['DOWN']));d=post-pre
        if d>EPS:
            self.expDebt=max(0.,self.expDebt)+d;self.peakDebt=max(self.peakDebt,self.expDebt);self.lastExpandT=int(t)
        elif d<-EPS and self.expDebt>EPS:
            self.expDebt=max(0.,self.expDebt-(-d))
            if self.expDebt<=EPS:self.expDebt=self.peakDebt=0.;self.lastExpandT=None
    def progress(self):
        if self.expDebt<=EPS or self.peakDebt<=EPS:return 1.
        return max(0.,min(1.,(self.peakDebt-self.expDebt)/self.peakDebt))
    def weak_side(self):
        u=float(self.inv['UP']);d=float(self.inv['DOWN'])
        return 'UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
    def would_expand(self,side,qty):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);pre=abs(u-d)
        if side=='UP':u+=float(qty)
        else:d+=float(qty)
        return abs(u-d)>pre+EPS
    def sup_features(self,t,pa,pup,action_th):
        weak=self.weak_side()
        if weak=='UP':p_rep=pup;p_exp=1-pup
        elif weak=='DOWN':p_rep=1-pup;p_exp=pup
        else:p_exp=max(pup,1-pup);p_rep=min(pup,1-pup)
        age=max(0,int(t)-int(self.lastExpandT)) if self.lastExpandT is not None else 0
        return np.asarray([self.progress(),math.log1p(age),p_exp-p_rep,pa-float(action_th)],float)
    def teacher_reexpand_label(self):
        # Target is a strict-past teacher only. It supplies desired Maker portfolio responsibility
        # on OUR current state; no Target state/threshold is exported to runtime.
        du=float(self.target['UP'])-float(self.inv['UP'])-float(self.reserved_authoritative('UP'))
        dd=float(self.target['DOWN'])-float(self.inv['DOWN'])-float(self.reserved_authoritative('DOWN'))
        if max(du,dd)<=EPS:return 0
        tside='UP' if du>=dd else 'DOWN'
        weak=self.weak_side()
        return 0 if weak is not None and tside==weak else 1

def collect_supervisor(tmp,cohort,traj,models):
    X=[];y=[];mids=[]
    train=[r for r in cohort if r['split']=='TRAIN40']
    for i,cr in enumerate(train,1):
        sim=LifeSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,traj.get(str(cr['marketId']),[]))
        try:
            ups=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);v1.ex.advance_to(sim.bt,first);end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
            for u in ups:
                t=int(u[1]);v1.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);ca=v1.apply(sim.book,u);sim.advance_target(t);qv=v1.quotes(sim.book)
                if not qv:continue
                if sim.firstValid is None:sim.firstValid=t
                if (end-t)/1000.<=180:continue
                sim.seed_if_needed(t,qv)
                if not sim.seeded:continue
                xx=sim.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(xx)[0,1])
                if pa<models['actionTh']:continue
                pup=float(models['side'].predict_proba(xx)[0,1]);side='UP' if pup>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(xx)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
                if sim.expDebt>EPS and sim.would_expand(side,qty):
                    X.append(sim.sup_features(t,pa,pup,models['actionTh']));y.append(sim.teacher_reexpand_label());mids.append(int(cr['marketId']))
                sim.submit(t,side,p,qty)
        finally:sim.close()
        if i%10==0:print(json.dumps({'supervisorCollectProgress':i,'rows':len(X),'positive':int(sum(y))}),flush=True)
    return np.asarray(X,float),np.asarray(y,int),np.asarray(mids,int)

def fit_supervisor(X,y,mids):
    ums=sorted(set(int(x) for x in mids));nva=max(3,min(10,int(round(len(ums)*0.25))));nva=min(nva,max(1,len(ums)-1));tr=set(ums[:-nva]);va=set(ums[-nva:]);it=np.where(np.isin(mids,list(tr)))[0];iv=np.where(np.isin(mids,list(va)))[0]
    kw=dict(max_iter=180,learning_rate=.05,max_leaf_nodes=9,min_samples_leaf=50,l2_regularization=8.,class_weight='balanced',random_state=23,monotonic_cst=[1,0,0,0])
    m=HistGradientBoostingClassifier(**kw).fit(X[it],y[it]);pv=m.predict_proba(X[iv])[:,1]
    val={'trainN':int(len(it)),'validationN':int(len(iv)),'positiveRateTrain':float(y[it].mean()),'positiveRateValidation':float(y[iv].mean()),'auc':float(roc_auc_score(y[iv],pv)) if len(set(y[iv]))>1 else None,'ap':float(average_precision_score(y[iv],pv)) if y[iv].sum()>0 else None}
    full=HistGradientBoostingClassifier(**kw).fit(X,y);return full,val

def run_market(tmp,cr,models,sup):
    sim=LifeSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,None)
    try:
        ups=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);v1.ex.advance_to(sim.bt,first);end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
        for u in ups:
            t=int(u[1]);v1.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);ca=v1.apply(sim.book,u);qv=v1.quotes(sim.book)
            if not qv:continue
            if sim.firstValid is None:sim.firstValid=t
            if (end-t)/1000.<=180:continue
            sim.seed_if_needed(t,qv)
            if not sim.seeded:continue
            xx=sim.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(xx)[0,1])
            if pa<models['actionTh']:continue
            pup=float(models['side'].predict_proba(xx)[0,1]);side='UP' if pup>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(xx)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
            if sim.expDebt>EPS and sim.would_expand(side,qty):
                pred=int(sup.predict(sim.sup_features(t,pa,pup,models['actionTh']).reshape(1,-1))[0])
                if pred==0:sim.supBlocks+=1;continue
                sim.supAllows+=1
            sim.submit(t,side,p,qty)
        end2=int(sim.meta['lastReceivedMs']);v1.ex.advance_to(sim.bt,end2);sim.process(end2);winner=str(cr['winner']).upper();opp='UP' if winner=='DOWN' else 'DOWN';pnl=sim.inv[winner]-sim.cost;gross=sum(sim.inv.values())
        return {'pnl':pnl,'oppositePnl':sim.inv[opp]-sim.cost,'buyNotional':sim.cost,'pairCoverage':2*min(sim.inv.values())/gross if gross>EPS else 0.,'floor':min(sim.inv.values())-sim.cost,'absNet':abs(sim.inv['UP']-sim.inv['DOWN']),'submits':sim.submits,'fills':sim.fills,'supBlocks':sim.supBlocks,'supAllows':sim.supAllows}
    finally:sim.close()

def agg(rs):
    p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs)
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>EPS for r in rs),'pnl':p,'buyNotional':b,'roi':p/b if b else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanSupervisorBlocks':statistics.mean(r['supBlocks'] for r in rs),'meanSupervisorAllows':statistics.mean(r['supAllows'] for r in rs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_onpolicy_lifecycle_sup_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);X,y,mids=collect_supervisor(tmp,cohort,traj,models);sup,val=fit_supervisor(X,y,mids);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            r=run_market(tmp,cr,models,sup);r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        s=agg(rows);out={'version':'ETH_ONPOLICY_LIFECYCLE_SUPERVISOR_V1','researchOnly':True,'boundary':['Base = frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation','Supervisor training states are OUR TRAIN40 on-policy states; Target strict-past Maker trajectory supplies lifecycle teacher label only','Supervisor features are OUR self-state only: repair progress, lifecycle age, DAgger role margin, DAgger action margin','Only dominant-side re-expansion while own expansion debt is outstanding is vetoable; weak-side repair never blocked','Repair-progress monotonicity is structural prior; no Target numeric threshold/state/classifier exported to runtime','Fresh101 development-only; no Target runtime input; <=180s no new exposure'],'supervisorFeatures':SUP_FEATURES,'supervisorRows':int(len(X)),'supervisorValidation':val,'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'supervisorRows':len(X),'supervisorValidation':val,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
