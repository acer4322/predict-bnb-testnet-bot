from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
import run_eth_target_repair_progress_authority_v1 as pa
EPS=1e-9

class SemanticReauthSim(pa.ProgressAuthoritySim):
    def __init__(self,tape,mode,models,iso):
        super().__init__(tape,mode,models,iso);self.intent={};self.semanticCancelRequests=0;self.semanticStaleSeen=0
    def role_now(self,side):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
        return 'REPAIR' if weak is not None and side==weak else 'EXPAND'
    def submit(self,t,side,p,q):
        n0=int(self.n);role=self.role_now(side);ok=super().submit(t,side,p,q)
        if ok is not False and int(self.n)>n0:self.intent[n0]={'role':role,'side':side,'submitT':int(t)}
        return ok
    def semantic_reauthorize(self,t):
        # A carrier admitted specifically as REPAIR may not silently become EXPAND.
        # Once own material state changes enough that its side is no longer weak, cancel the
        # still-live remainder and let a future decision create a new objective if warranted.
        for o in self.orders.values():
            n=int(o['n']);meta=self.intent.get(n)
            if not meta or meta['role']!='REPAIR':continue
            s=self.snap(o)
            if not v1.live(s.get('status')):continue
            if self.role_now(o['side'])=='REPAIR':continue
            self.semanticStaleSeen+=1
            cur=self.bt.orders(0).get(n)
            if cur is not None and bool(cur.cancellable):
                try:self.bt.cancel(0,n,False);self.semanticCancelRequests+=1
                except Exception:pass
    def run_student_semantic(self,models,winner):
        ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in ups:
            t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.semantic_reauthorize(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
            if not qv:continue
            if self.firstValid is None:self.firstValid=t
            if (end-t)/1000.<=180:continue
            self.seed_if_needed(t,qv)
            if not self.seeded:continue
            x=self.features(t,qv,ca,end).reshape(1,-1);pa0=float(models['action'].predict_proba(x)[0,1])
            if pa0<models['actionTh']:continue
            ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
            if self.expDebt>v1.EPS and self.would_expand(side,qty):
                prog=self.repair_progress();auth=float(self.iso.predict([prog])[0])
                if auth<0.5:self.progressBlocks+=1;continue
                self.progressAllows+=1
            self.submit(t,side,p,qty)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
        return {'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'floor':min(self.inv.values())-self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'semanticCancelRequests':self.semanticCancelRequests,'semanticStaleSeen':self.semanticStaleSeen,'progressBlocks':self.progressBlocks,'progressAllows':self.progressAllows}

def agg(rs):
    p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs)
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>EPS for r in rs),'pnl':p,'buyNotional':b,'roi':p/b if b else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanSemanticCancelRequests':statistics.mean(r['semanticCancelRequests'] for r in rs),'meanSemanticStaleSeen':statistics.mean(r['semanticStaleSeen'] for r in rs),'meanProgressBlocks':statistics.mean(r['progressBlocks'] for r in rs),'meanProgressAllows':statistics.mean(r['progressAllows'] for r in rs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_semantic_reauth_v1_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);iso,teacher=pa.build_iso(a.target_db);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=SemanticReauthSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,iso)
            try:r=sim.run_student_semantic(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows),'semanticCancels':sum(x['semanticCancelRequests'] for x in rows)}),flush=True)
        s=agg(rows);out={'version':'ETH_SEMANTIC_REAUTHORIZATION_V1','boundary':['Base = Target Repair Progress Authority V1 + Local Pending Reservation','No Target numeric threshold added beyond frozen progress-authority baseline','Structural invariant only: a live carrier admitted as REPAIR is cancelled when authoritative own inventory changes such that its side is no longer weak; later re-entry requires a fresh student decision','EXPAND->REPAIR drift is not cancelled; no timing/qty/side threshold sweep','Fresh101 development-only; <=180s no new exposure'],'targetProgressTeacher':teacher,'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
