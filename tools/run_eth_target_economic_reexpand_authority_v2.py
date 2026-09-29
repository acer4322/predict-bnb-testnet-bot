from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
import analyze_target_eth_reexpand_authority_economic_v8 as audit

class EconomicAuthoritySim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models,auth):
        super().__init__(tape,mode,models);self.auth=auth;self.expDebt=0.;self.lastExpandDebt=0.;self.lastExpandT=None;self.lastTransitionExpand=0.;self.blocks=0;self.allows=0
    def record_fill(self,t,side,q,p):
        pre=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        super().record_fill(t,side,q,p)
        post=abs(float(self.inv['UP'])-float(self.inv['DOWN']));d=post-pre
        if d>v1.EPS:
            self.expDebt=max(0.,self.expDebt)+d;self.lastExpandDebt=self.expDebt;self.lastExpandT=int(t);self.lastTransitionExpand=1.
        elif d<-v1.EPS and self.expDebt>v1.EPS:
            self.expDebt=max(0.,self.expDebt-(-d));self.lastTransitionExpand=0.
            if self.expDebt<=v1.EPS:self.expDebt=self.lastExpandDebt=0.;self.lastExpandT=None
    def would_expand(self,side,qty):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);pre=abs(u-d)
        if side=='UP':u+=qty
        else:d+=qty
        return abs(u-d)>pre+v1.EPS
    def authority_features(self,t):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);gross=u+d;gap=abs(u-d);paired=min(u,d);cost=float(self.cost)
        progress=max(0.,min(1.,(self.lastExpandDebt-self.expDebt)/self.lastExpandDebt)) if self.expDebt>v1.EPS and self.lastExpandDebt>v1.EPS else 1.
        weak='UP' if u<d-v1.EPS else 'DOWN' if d<u-v1.EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
        au=float(self.sideCost['UP'])/u if u>v1.EPS else 0.;ad=float(self.sideCost['DOWN'])/d if d>v1.EPS else 0.
        wav=au if weak=='UP' else ad if weak=='DOWN' else 0.;dav=ad if dom=='DOWN' else au if dom=='UP' else 0.
        return np.asarray([[progress,self.expDebt/max(gross,1.),2*paired/gross if gross>v1.EPS else 0.,(paired-cost)/max(cost,1.),(max(u,d)-cost)/max(cost,1.),gap/max(gross,1.),float((int(t)-int(self.lastExpandT)) if self.lastExpandT is not None else 60000),self.lastTransitionExpand,wav,dav]],float)
    def run_student_auth(self,models,winner):
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
            if self.expDebt>v1.EPS and self.would_expand(side,qty):
                prob=float(self.auth.predict_proba(self.authority_features(t))[0,1])
                if prob<.5:self.blocks+=1;continue
                self.allows+=1
            self.submit(t,side,p,qty)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
        return {'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>v1.EPS else 0.,'floor':min(self.inv.values())-self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'blocks':self.blocks,'allows':self.allows}

def agg(rs):
    p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs)
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>v1.EPS for r in rs),'pnl':p,'buyNotional':b,'roi':p/b if b else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanBlocks':statistics.mean(r['blocks'] for r in rs),'meanAllows':statistics.mean(r['allows'] for r in rs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_econ_auth_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj)
        rr=audit.build_rows(a.target_db);X=np.asarray([r['x'] for r in rr],float);y=np.asarray([r['y'] for r in rr],int);auth=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=50,l2_regularization=4.0,random_state=17).fit(X,y)
        test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=EconomicAuthoritySim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,auth)
            try:r=sim.run_student_auth(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        s=agg(rows);out={'version':'ETH_TARGET_ECONOMIC_REEXPAND_AUTHORITY_V2','boundary':['Base frozen DAgger + BOOK_IMBALANCE + Local Pending Reservation','Frozen Target ETH Maker-only strict-past economic/objective authority model; only outstanding-debt dominant re-expansion vetoable','Weak-side repair never blocked; no action timing/qty changes; p>=0.5 fixed, no threshold sweep','Fresh101 development-only; <=180s no new exposure'],'authorityRows':len(rr),'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
