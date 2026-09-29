from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

MODES=('CREDIT_CAP','CREDIT_UNLOCK')

class CreditSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models,credit_mode):
        super().__init__(tape,mode,models)
        self.creditMode=credit_mode
        self.expDebt=0.0
        self.repairCredit=0.0
        self.tracking=False
        self.creditAdds=0.0
        self.creditSpends=0.0
        self.creditHolds=0
        self.creditAllows=0
        self.creditAttempts=0
        self.creditSamples=[]
    def effective_side(self,side):
        return float(self.inv[side])+float(self.reserved_authoritative(side))
    def would_expand_effective(self,side,qty):
        u=self.effective_side('UP');d=self.effective_side('DOWN');pre=abs(u-d)
        if side=='UP':u+=float(qty)
        else:d+=float(qty)
        return abs(u-d)>pre+v1.EPS
    def record_fill(self,t,side,q,p):
        pre=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        debt_before=float(self.expDebt)
        super().record_fill(t,side,q,p)
        post=abs(float(self.inv['UP'])-float(self.inv['DOWN']));delta=post-pre
        if delta>v1.EPS:
            self.expDebt=max(0.0,self.expDebt)+delta
            if self.tracking and self.repairCredit>v1.EPS:
                spend=min(self.repairCredit,delta)
                self.repairCredit-=spend
                self.creditSpends+=spend
        elif delta<-v1.EPS and debt_before>v1.EPS:
            repair=min(debt_before,-delta)
            self.expDebt=max(0.0,self.expDebt-repair)
            self.repairCredit+=repair
            self.creditAdds+=repair
            self.tracking=True
            if self.expDebt<=v1.EPS:
                self.expDebt=0.0
                self.repairCredit=0.0
                self.tracking=False
        self.creditSamples.append(float(self.repairCredit))
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
            qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid'])
            legal=1/p if p>0 else 1e9;qty=max(qty,legal);qty=min(qty,12.)
            send=qty
            if self.tracking and self.expDebt>v1.EPS and self.would_expand_effective(side,qty):
                self.creditAttempts+=1
                if self.creditMode=='CREDIT_CAP':
                    send=min(qty,float(self.repairCredit))
                    if send+v1.EPS<legal:
                        self.creditHolds+=1;continue
                elif self.creditMode=='CREDIT_UNLOCK':
                    if self.repairCredit+v1.EPS<legal:
                        self.creditHolds+=1;continue
                self.creditAllows+=1
            self.submit(t,side,p,send)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)
        pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
        return {'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>v1.EPS else 0.,'floor':min(self.inv.values())-self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'creditAdds':self.creditAdds,'creditSpends':self.creditSpends,'creditHolds':self.creditHolds,'creditAllows':self.creditAllows,'creditAttempts':self.creditAttempts,'meanCredit':statistics.mean(self.creditSamples) if self.creditSamples else 0.0}

def agg(rs):
    p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs)
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>v1.EPS for r in rs),'pnl':p,'buyNotional':b,'roi':p/b if b else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanCreditAdds':statistics.mean(r['creditAdds'] for r in rs),'meanCreditSpends':statistics.mean(r['creditSpends'] for r in rs),'meanCreditHolds':statistics.mean(r['creditHolds'] for r in rs),'meanCreditAllows':statistics.mean(r['creditAllows'] for r in rs),'meanCreditAttempts':statistics.mean(r['creditAttempts'] for r in rs),'meanCredit':statistics.mean(r['meanCredit'] for r in rs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--mode',required=True,choices=MODES);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='eth_ext_reset_credit_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=CreditSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,a.mode)
            try:r=sim.run_student_credit(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'mode':a.mode,'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        s=agg(rows);out={'version':'ETH_EXTERNAL_RESET_REPAIR_CREDIT_V3','mode':a.mode,'boundary':['Base frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation','No Target runtime thresholds/classifier; Target is architecture reference only','External-reset/conditional-integration analogy: re-expansion authority state is updated only by actual materialized repair and actual materialized expansion, never nominal waiting commands','Credit persists across sub-minimum fills; no fixed cooldown or threshold sweep','CREDIT_CAP caps dominant re-expansion qty by unspent materialized repair credit; CREDIT_UNLOCK only requires enough credit for one legal minimum order','<=180s no new exposure'],'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'mode':a.mode,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
