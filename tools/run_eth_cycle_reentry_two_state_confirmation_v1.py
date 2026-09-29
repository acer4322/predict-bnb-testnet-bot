from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp


def weak_side(inv):
    u=float(inv['UP']); d=float(inv['DOWN'])
    if u<d-v1.EPS:return 'UP'
    if d<u-v1.EPS:return 'DOWN'
    return None

class ConfirmSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models):
        super().__init__(tape,mode,models)
        self.boundaryTick=None; self.boundaryCount=0
        self.reentryPending=False; self.confirmSide=None
        self.confirmBlocks=0; self.confirmedReentries=0; self.confirmResets=0
    def process(self,t):
        pre_inv=dict(self.inv); pre_weak=weak_side(self.inv)
        pre_ru=float(self.reserved_authoritative('UP')); pre_rd=float(self.reserved_authoritative('DOWN'))
        super().process(t)
        post_weak=weak_side(self.inv)
        du=float(self.inv['UP'])-float(pre_inv['UP']); dd=float(self.inv['DOWN'])-float(pre_inv['DOWN'])
        post_ru=float(self.reserved_authoritative('UP')); post_rd=float(self.reserved_authoritative('DOWN'))
        fill=(du>v1.EPS or dd>v1.EPS)
        release=(pre_ru>v1.EPS and post_ru<=v1.EPS) or (pre_rd>v1.EPS and post_rd<=v1.EPS)
        flip=(pre_weak is not None and post_weak is not None and pre_weak!=post_weak)
        if fill or release or flip:
            self.boundaryTick=int(t);self.boundaryCount+=1;self.reentryPending=True
            if self.confirmSide is not None:self.confirmResets+=1
            self.confirmSide=None
    def run_student_confirm(self,models,winner):
        ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in ups:
            t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
            if not qv:continue
            if self.firstValid is None:self.firstValid=t
            if (end-t)/1000.<=180:continue
            self.seed_if_needed(t,qv)
            if not self.seeded:continue
            # Any transition observed on this receipt invalidates confirmation for this same state.
            if self.boundaryTick==t:
                self.confirmBlocks+=1
                continue
            x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
            if pa<models['actionTh']:
                if self.reentryPending and self.confirmSide is not None:
                    self.confirmResets+=1;self.confirmSide=None
                continue
            ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN'
            qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
            if self.reentryPending:
                if self.confirmSide!=side:
                    if self.confirmSide is not None:self.confirmResets+=1
                    self.confirmSide=side;self.confirmBlocks+=1
                    continue
                accepted=bool(self.submit(t,side,p,qty))
                if accepted:
                    self.reentryPending=False;self.confirmSide=None;self.confirmedReentries+=1
                continue
            self.submit(t,side,p,qty)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)
        pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
        return {'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>v1.EPS else 0.,'floor':min(self.inv.values())-self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'up':self.inv['UP'],'down':self.inv['DOWN'],'boundaries':self.boundaryCount,'confirmBlocks':self.confirmBlocks,'confirmedReentries':self.confirmedReentries,'confirmResets':self.confirmResets,'duplicateBlocked':self.duplicateBlocked,'localPendingBlocked':self.localPendingBlocked}

def agg(rs):
    buy=sum(r['buyNotional'] for r in rs);p=sum(r['pnl'] for r in rs)
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>v1.EPS for r in rs),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanBoundaries':statistics.mean(r['boundaries'] for r in rs),'meanConfirmBlocks':statistics.mean(r['confirmBlocks'] for r in rs),'meanConfirmedReentries':statistics.mean(r['confirmedReentries'] for r in rs),'meanConfirmResets':statistics.mean(r['confirmResets'] for r in rs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_reentry_confirm_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=ConfirmSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
            try:r=sim.run_student_confirm(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner'],'targetPnl':cr.get('targetPnl'),'targetBuy':cr.get('targetBuy')});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        summary=agg(rows);out={'version':'ETH_CYCLE_REENTRY_TWO_STATE_CONFIRMATION_V1','researchOnly':True,'boundary':['Base=frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation','After own fill progress, authoritative carrier release, or weak-side flip, re-entry requires the frozen base model to propose an action on two consecutive distinct receipt-clock states with the same side and no intervening lifecycle boundary','No fixed millisecond cooldown; a HOLD proposal, side change, or new lifecycle boundary resets confirmation','No threshold/model sweep; Fresh101 consumed development cohort only','No Target future/objective/winner enters runtime decisions; <=180s no new exposure'],'round1Offline':off1,'round2Offline':off2,'summary':summary,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
