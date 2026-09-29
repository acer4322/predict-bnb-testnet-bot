from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,sqlite3,statistics,math
from pathlib import Path
import numpy as np
from sklearn.isotonic import IsotonicRegression
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

class ProgressAuthoritySim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models,iso):
        super().__init__(tape,mode,models);self.iso=iso;self.expDebt=0.0;self.lastExpandDebt=0.0;self.progressBlocks=0;self.progressAllows=0
    def record_fill(self,t,side,q,p):
        pre=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        super().record_fill(t,side,q,p)
        post=abs(float(self.inv['UP'])-float(self.inv['DOWN']));d=post-pre
        if d>v1.EPS:
            self.expDebt=max(0.0,self.expDebt)+d;self.lastExpandDebt=self.expDebt
        elif d<-v1.EPS and self.expDebt>v1.EPS:
            self.expDebt=max(0.0,self.expDebt-(-d))
            if self.expDebt<=v1.EPS:self.expDebt=0.0;self.lastExpandDebt=0.0
    def repair_progress(self):
        if self.expDebt<=v1.EPS or self.lastExpandDebt<=v1.EPS:return 1.0
        return max(0.0,min(1.0,(self.lastExpandDebt-self.expDebt)/self.lastExpandDebt))
    def would_expand(self,side,qty):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);pre=abs(u-d)
        if side=='UP':u+=float(qty)
        else:d+=float(qty)
        return abs(u-d)>pre+v1.EPS
    def run_student_progress(self,models,winner):
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
                prog=self.repair_progress();auth=float(self.iso.predict([prog])[0])
                if auth<0.5:self.progressBlocks+=1;continue
                self.progressAllows+=1
            self.submit(t,side,p,qty)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
        return {'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>v1.EPS else 0.,'floor':min(self.inv.values())-self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'up':self.inv['UP'],'down':self.inv['DOWN'],'progressBlocks':self.progressBlocks,'progressAllows':self.progressAllows}

def build_iso(db):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    rows=list(c.execute("select parent_id,market_id,side,first_event_ms,shares from target_parent_orders where asset='ETH' and role='MAKER' order by market_id,first_event_ms,parent_id"));c.close()
    by={}
    for r in rows:by.setdefault(int(r['market_id']),[]).append(r)
    X=[];y=[]
    for mid,zz in by.items():
        u=d=0.0;debt=0.0;base=0.0
        for r in zz:
            sh=float(r['shares']);pre=abs(u-d)
            if r['side']=='UP':u+=sh
            else:d+=sh
            post=abs(u-d);delta=post-pre
            if debt>v1.EPS and base>v1.EPS and abs(delta)>v1.EPS:
                X.append(max(0.0,min(1.0,(base-debt)/base)));y.append(1 if delta>0 else 0)
            if delta>v1.EPS:debt=max(0.0,debt)+delta;base=debt
            elif delta<-v1.EPS and debt>v1.EPS:
                debt=max(0.0,debt-(-delta))
                if debt<=v1.EPS:debt=0.0;base=0.0
    iso=IsotonicRegression(y_min=0.0,y_max=1.0,increasing=True,out_of_bounds='clip').fit(np.asarray(X),np.asarray(y))
    grid=[0,.1,.25,.5,.75,1.0];pred=[float(z) for z in iso.predict(grid)]
    crossing=next((g for g,p in zip(np.linspace(0,1,1001),iso.predict(np.linspace(0,1,1001))) if p>=.5),None)
    return iso,{'rows':len(X),'expandRate':float(np.mean(y)),'grid':dict(zip([str(g) for g in grid],pred)),'p50CrossingProgress':float(crossing) if crossing is not None else None}

def agg(rs):
    buy=sum(r['buyNotional'] for r in rs);p=sum(r['pnl'] for r in rs)
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>v1.EPS for r in rs),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanProgressBlocks':statistics.mean(r['progressBlocks'] for r in rs),'meanProgressAllows':statistics.mean(r['progressAllows'] for r in rs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_target_progress_authority_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);iso,teacher=build_iso(a.target_db);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=ProgressAuthoritySim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,iso)
            try:r=sim.run_student_progress(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        summary=agg(rows);out={'version':'ETH_TARGET_REPAIR_PROGRESS_AUTHORITY_V1','boundary':['Base frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation','Frozen Target ETH Maker-only actual-fill isotonic mapping repairProgress -> re-expand probability','Only dominant-side re-expansion while expansion debt is outstanding is vetoable; weak-side repair is never blocked by phase authority','No Target future/objective/winner at runtime; frozen Target snapshot ends before Fresh101','No threshold sweep: authority boundary fixed at Target majority p(reexpand)>=0.5','<=180s no new exposure'],'targetProgressTeacher':teacher,'round1Offline':off1,'round2Offline':off2,'summary':summary,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'teacher':teacher,'summary':summary},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
