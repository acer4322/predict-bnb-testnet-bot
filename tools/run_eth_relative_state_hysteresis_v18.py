from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
EPS=1e-9

class RelativeHysteresisSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models):
        super().__init__(tape,mode,models)
        self.phase=None
        self.repairSide=None
        self.anchorGap=None
        self.anchorPair=None
        self.hystBlocks=0
        self.hystAllows=0
        self.phaseEntries=0
        self.phaseResets=0
        self.unfilledResets=0

    def state_shape(self):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);gross=u+d
        gap=abs(u-d)
        pair=2.0*min(u,d)/gross if gross>EPS else 0.0
        weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
        return gap,pair,weak

    def role_now(self,side):
        _,_,weak=self.state_shape()
        return 'REPAIR' if weak is not None and side==weak else 'EXPAND'

    def maybe_reset_unfilled_repair(self):
        if self.phase!='REPAIR' or self.repairSide is None:return
        gap,pair,_=self.state_shape()
        improved=(self.anchorGap is not None and gap<self.anchorGap-EPS and self.anchorPair is not None and pair>self.anchorPair+EPS)
        if improved:return
        if self.reserved_authoritative(self.repairSide)<=EPS:
            self.phase=None;self.repairSide=None;self.anchorGap=None;self.anchorPair=None
            self.phaseResets+=1;self.unfilledResets+=1

    def enter_repair_phase(self,side):
        if self.phase=='REPAIR':return
        gap,pair,_=self.state_shape()
        self.phase='REPAIR';self.repairSide=side;self.anchorGap=gap;self.anchorPair=pair;self.phaseEntries+=1

    def repair_crossed_entry_state(self):
        if self.phase!='REPAIR':return True
        gap,pair,_=self.state_shape()
        return gap<self.anchorGap-EPS and pair>self.anchorPair+EPS

    def reset_to_expand(self):
        self.phase=None;self.repairSide=None;self.anchorGap=None;self.anchorPair=None;self.phaseResets+=1

    def run_student_hysteresis(self,models,winner):
        ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in ups:
            t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);self.maybe_reset_unfilled_repair();ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
            if not qv:continue
            if self.firstValid is None:self.firstValid=t
            if (end-t)/1000.<=180:continue
            self.seed_if_needed(t,qv)
            if not self.seeded:continue
            x=self.features(t,qv,ca,end).reshape(1,-1)
            pa=float(models['action'].predict_proba(x)[0,1])
            if pa<models['actionTh']:continue
            ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN'
            qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))))
            p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
            role=self.role_now(side)
            if role=='EXPAND' and self.phase=='REPAIR':
                if not self.repair_crossed_entry_state():
                    self.hystBlocks+=1;continue
                self.hystAllows+=1
            ok=self.submit(t,side,p,qty)
            if ok is False:continue
            if role=='REPAIR':self.enter_repair_phase(side)
            elif role=='EXPAND' and self.phase=='REPAIR':self.reset_to_expand()
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)
        pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
        return {'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,
                'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'floor':min(self.inv.values())-self.cost,
                'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,
                'hystBlocks':self.hystBlocks,'hystAllows':self.hystAllows,'phaseEntries':self.phaseEntries,
                'phaseResets':self.phaseResets,'unfilledResets':self.unfilledResets}

def agg(rs):
    p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs)
    def mn(k):return statistics.mean(r[k] for r in rs)
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>EPS for r in rs),'pnl':p,'buyNotional':b,'roi':p/b if b else None,
            'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':mn('buyNotional'),'meanPairCoverage':mn('pairCoverage'),
            'meanAbsNet':mn('absNet'),'meanSubmits':mn('submits'),'meanFills':mn('fills'),'maxWin':max(r['pnl'] for r in rs),
            'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),
            'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanHystBlocks':mn('hystBlocks'),
            'meanHystAllows':mn('hystAllows'),'meanPhaseEntries':mn('phaseEntries'),'meanPhaseResets':mn('phaseResets'),
            'meanUnfilledResets':mn('unfilledResets')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='eth_relative_hyst_v18_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'))
        models,off1,off2=lp.train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=RelativeHysteresisSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
            try:r=sim.run_student_hysteresis(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        s=agg(rows);out={'version':'ETH_RELATIVE_STATE_HYSTERESIS_V18','researchOnly':True,
            'boundary':['Base=frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation','No Target numeric threshold/classifier at runtime','Target V17 used only as qualitative evidence that E->R and R->E occupy different normalized state regions','REPAIR phase begins only when OUR actually submits a weak-side repair carrier; zero-repair continuation remains possible','R->E requires OUR own gap to fall below and pair coverage to rise above the state observed at REPAIR entry','No fixed dwell/cooldown/margin sweep; unfilled repair intent resets when no authoritative pending carrier remains','<=180s no new exposure; Fresh101 development-only'],
            'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
