from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

class RepairCreditTrackingSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models):
        super().__init__(tape,mode,models)
        self.expDebt=0.0
        self.repairCredit=0.0
        self.creditAdded=0.0
        self.creditConsumed=0.0
        self.creditAttempts=0
        self.creditSubmits=0
        self.creditHeldBelowMin=0
        self.creditHist=[]

    def record_fill(self,t,side,q,p):
        pre=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        was_debt=self.expDebt>v1.EPS
        super().record_fill(t,side,q,p)
        post=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        delta=post-pre
        if delta>v1.EPS:
            if not was_debt:
                # New expansion episode: previous transition credit cannot leak across episodes.
                self.repairCredit=0.0
            self.expDebt=max(0.0,self.expDebt)+delta
            used=min(self.repairCredit,delta)
            self.repairCredit=max(0.0,self.repairCredit-used)
            self.creditConsumed+=used
        elif delta<-v1.EPS and self.expDebt>v1.EPS:
            repaired=min(self.expDebt,-delta)
            self.expDebt=max(0.0,self.expDebt-repaired)
            self.repairCredit+=repaired
            self.creditAdded+=repaired
            if self.expDebt<=v1.EPS:
                # Debt fully cleared: return to nominal controller, no stale transition credit.
                self.expDebt=0.0
                self.repairCredit=0.0
        self.creditHist.append(self.repairCredit)

    def would_expand(self,side,qty):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);pre=abs(u-d)
        if side=='UP':u+=float(qty)
        else:d+=float(qty)
        return abs(u-d)>pre+v1.EPS

    def run_student_credit(self,models,winner):
        ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in ups:
            t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
            if not qv:continue
            if self.firstValid is None:self.firstValid=t
            if (end-t)/1000.<=180:continue
            self.seed_if_needed(t,qv)
            if not self.seeded:continue
            x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
            if pa<models['actionTh']:continue
            ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN'
            desired=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);legal=1/p if p>0 else 1e9
            desired=max(desired,legal);desired=min(desired,12.);sent=desired
            if self.expDebt>v1.EPS and self.would_expand(side,desired):
                self.creditAttempts+=1
                sent=min(desired,self.repairCredit)
                if sent+v1.EPS<legal:
                    self.creditHeldBelowMin+=1
                    continue
                self.creditSubmits+=1
            self.submit(t,side,p,sent)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)
        pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
        return {'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,
                'pairCoverage':2*min(self.inv.values())/gross if gross>v1.EPS else 0.,'floor':min(self.inv.values())-self.cost,
                'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'up':self.inv['UP'],'down':self.inv['DOWN'],
                'creditAttempts':self.creditAttempts,'creditSubmits':self.creditSubmits,'creditHeldBelowMin':self.creditHeldBelowMin,
                'creditAdded':self.creditAdded,'creditConsumed':self.creditConsumed,'terminalCredit':self.repairCredit,
                'meanCredit':statistics.mean(self.creditHist) if self.creditHist else 0.0}

def agg(rs):
    p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs)
    def mn(k):return statistics.mean(r[k] for r in rs)
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>v1.EPS for r in rs),'pnl':p,'buyNotional':b,'roi':p/b if b else None,
            'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':mn('buyNotional'),'meanPairCoverage':mn('pairCoverage'),'meanAbsNet':mn('absNet'),
            'meanSubmits':mn('submits'),'meanFills':mn('fills'),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),
            'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),
            'meanCreditAttempts':mn('creditAttempts'),'meanCreditSubmits':mn('creditSubmits'),'meanCreditHeldBelowMin':mn('creditHeldBelowMin'),
            'meanCreditAdded':mn('creditAdded'),'meanCreditConsumed':mn('creditConsumed'),'meanTerminalCredit':mn('terminalCredit'),'meanCredit':mn('meanCredit')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_bumpless_credit_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'))
        models,off1,off2=lp.train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=RepairCreditTrackingSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
            try:r=sim.run_student_credit(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        s=agg(rows);out={'version':'ETH_BUMPLESS_REPAIR_CREDIT_TRACKING_V3','researchOnly':True,
            'boundary':['Base = frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation','No Target numeric threshold/classifier','Weak-side repair always passes','Each materialized reduction in abs-net adds equal repair credit; dominant re-expansion while debt is outstanding may submit at most available repair credit','Credit is consumed only by materialized expansion and reset when a new debt episode starts or debt fully clears','No per-receipt accumulation; below-minimum credit persists until own fills add enough credit','Fresh101 development-only; <=180s no new exposure'],
            'matureResearchMapping':{'trackingBackCalculation':'inactive re-expansion authority is back-calculated from actual repair output, not stale desired commands','bumplessTransfer':'authority transfers gradually as the outgoing repair mode produces real output','referenceGovernor':'nominal dominant command is clipped by a stateful admissible transition credit rather than a Target threshold'},
            'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
