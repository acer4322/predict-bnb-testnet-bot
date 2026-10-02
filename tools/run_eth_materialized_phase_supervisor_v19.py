from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
EPS=1e-9

class MaterializedPhaseSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models):
        super().__init__(tape,mode,models)
        self.repairOwners=set();self.repairPhase=False;self.repairResolutionT=None
        self.materializedRepairEntries=0;self.expandBlocksLiveRepair=0
        self.expandBlocksSameReceipt=0;self.phaseReentries=0;self.firstFillSeen=set()

    def role_now(self,side):
        u=float(self.inv['UP']);d=float(self.inv['DOWN'])
        weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
        return 'REPAIR' if weak is not None and side==weak else 'EXPAND'

    def process(self,t):
        for o in self.orders.values():
            s=self.snap(o);cum=float(s.get('cumExecQty') or 0);inc=max(0.,cum-o['cum'])
            if inc>EPS:
                pre_role=self.role_now(o['side'])
                self.record_fill(t,o['side'],inc,v1.fill_price(o['side'],s,o['price']))
                self.fills+=1;o['cum']=cum
                n=int(o['n'])
                if n not in self.firstFillSeen:
                    self.firstFillSeen.add(n)
                    if pre_role=='REPAIR':
                        self.repairOwners.add(n);self.repairPhase=True;self.repairResolutionT=None
                        self.materializedRepairEntries+=1
            o['status']=s.get('status')
        for side in ('UP','DOWN'):
            for key in list(self.localPending[side]):
                o=self.orders.get(key)
                if o is None:
                    self.localPending[side].pop(key,None);continue
                s=self.snap(o)
                if s.get('status') is not None:self.localPending[side].pop(key,None)
        if self.repairOwners:
            live_owners=set()
            for n in list(self.repairOwners):
                oo=next((o for o in self.orders.values() if int(o['n'])==int(n)),None)
                if oo is None:continue
                if v1.live(self.snap(oo).get('status')):live_owners.add(n)
            if not live_owners:
                self.repairOwners.clear()
                if self.repairPhase and self.repairResolutionT is None:self.repairResolutionT=int(t)
            else:self.repairOwners=live_owners

    def run_student_materialized_phase(self,models,winner):
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
            qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))))
            p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
            role=self.role_now(side)
            if role=='EXPAND' and self.repairPhase:
                if self.repairOwners:self.expandBlocksLiveRepair+=1;continue
                if self.repairResolutionT is not None and int(t)<=int(self.repairResolutionT):
                    self.expandBlocksSameReceipt+=1;continue
                self.repairPhase=False;self.repairResolutionT=None;self.phaseReentries+=1
            self.submit(t,side,p,qty)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)
        pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
        return {'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,
                'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'floor':min(self.inv.values())-self.cost,
                'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,
                'materializedRepairEntries':self.materializedRepairEntries,'expandBlocksLiveRepair':self.expandBlocksLiveRepair,
                'expandBlocksSameReceipt':self.expandBlocksSameReceipt,'phaseReentries':self.phaseReentries}

def agg(rs):
    p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs)
    def mn(k):return statistics.mean(r[k] for r in rs)
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>EPS for r in rs),'pnl':p,'buyNotional':b,'roi':p/b if b else None,
            'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':mn('buyNotional'),'meanPairCoverage':mn('pairCoverage'),
            'meanAbsNet':mn('absNet'),'meanSubmits':mn('submits'),'meanFills':mn('fills'),'maxWin':max(r['pnl'] for r in rs),
            'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),
            'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanMaterializedRepairEntries':mn('materializedRepairEntries'),
            'meanExpandBlocksLiveRepair':mn('expandBlocksLiveRepair'),'meanExpandBlocksSameReceipt':mn('expandBlocksSameReceipt'),
            'meanPhaseReentries':mn('phaseReentries')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='eth_materialized_phase_v19_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'))
        models,off1,off2=lp.train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=MaterializedPhaseSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
            try:r=sim.run_student_materialized_phase(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        s=agg(rows)
        out={'version':'ETH_MATERIALIZED_PHASE_SUPERVISOR_V19','researchOnly':True,
             'boundary':['Base=frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation','No Target numeric threshold/classifier at runtime','Target V14/V17 used only as structural evidence: ETH phase overlap is low and phase identity is defined on actual-filled parent events','REPAIR phase begins only on first actual fill of a carrier whose side is weak immediately before that fill; submit intent alone never creates phase','While such repair carrier remains venue-live, new EXPAND submits are blocked; weak-side repair remains allowed','When all materialized repair carriers resolve, EXPAND is not re-evaluated until a later receipt timestamp; then old phase authority is cleared and frozen DAgger decides from fresh authoritative state','Zero-repair continuation remains possible; no fixed dwell/cooldown/margin sweep; <=180s no new exposure; Fresh101 development-only'],
             'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
