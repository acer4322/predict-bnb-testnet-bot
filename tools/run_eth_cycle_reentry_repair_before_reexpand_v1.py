from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

class CycleGuardSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models):
        super().__init__(tape,mode,models)
        self.reexpandBlockedSide=None
        self.reexpandBlocks=0
        self.reexpandLocks=0
        self.repairUnlocks=0
    def process(self,t):
        pre_u=float(self.inv['UP']); pre_d=float(self.inv['DOWN']); pre_abs=abs(pre_u-pre_d)
        super().process(t)
        post_u=float(self.inv['UP']); post_d=float(self.inv['DOWN']); post_abs=abs(post_u-post_d)
        if post_abs > pre_abs + v1.EPS:
            side='UP' if post_u>post_d else 'DOWN' if post_d>post_u else None
            if side is not None:
                if self.reexpandBlockedSide != side:self.reexpandLocks+=1
                self.reexpandBlockedSide=side
        elif post_abs < pre_abs - v1.EPS:
            if self.reexpandBlockedSide is not None:
                self.repairUnlocks+=1
                self.reexpandBlockedSide=None
    def guarded_submit(self,t,side,p,q):
        if self.reexpandBlockedSide==side:
            self.reexpandBlocks+=1
            return False
        return self.submit(t,side,p,q)

def train_models(tmp,cohort,traj):
    return lp.train_models(tmp,cohort,traj)

def run_student(sim,models,winner):
    ups=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
    first=int(sim.meta['firstReceivedMs']);v1.ex.advance_to(sim.bt,first)
    end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
    for u in ups:
        t=int(u[1]);v1.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);ca=v1.apply(sim.book,u);qv=v1.quotes(sim.book)
        if not qv:continue
        if sim.firstValid is None:sim.firstValid=t
        if (end-t)/1000.<=180:continue
        sim.seed_if_needed(t,qv)
        if not sim.seeded:continue
        x=sim.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
        if pa<models['actionTh']:continue
        ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN'
        qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.)
        sim.guarded_submit(t,side,p,qty)
    end2=int(sim.meta['lastReceivedMs']);v1.ex.advance_to(sim.bt,end2);sim.process(end2)
    win=str(winner).upper();opp='UP' if win=='DOWN' else 'DOWN';pnl=float(sim.inv[win]-sim.cost);opp_pnl=float(sim.inv[opp]-sim.cost);gross=sum(sim.inv.values())
    return {'pnl':pnl,'oppositePnl':opp_pnl,'buyNotional':float(sim.cost),'pairCoverage':2*min(sim.inv.values())/gross if gross>v1.EPS else 0.,'floor':min(sim.inv.values())-sim.cost,'absNet':abs(sim.inv['UP']-sim.inv['DOWN']),'submits':sim.submits,'fills':sim.fills,'up':sim.inv['UP'],'down':sim.inv['DOWN'],'reexpandBlocks':sim.reexpandBlocks,'reexpandLocks':sim.reexpandLocks,'repairUnlocks':sim.repairUnlocks}

def agg(rows):
    buy=sum(r['buyNotional'] for r in rows);p=sum(r['pnl'] for r in rows);active=[r for r in rows if r['buyNotional']>v1.EPS]
    return {'markets':len(rows),'activeMarkets':len(active),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'winRate':sum(r['pnl']>0 for r in rows)/len(rows),'meanBuy':statistics.mean(r['buyNotional'] for r in rows),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rows),'positiveFloorRate':sum(r['floor']>=0 for r in rows)/len(rows),'meanAbsNet':statistics.mean(r['absNet'] for r in rows),'meanSubmits':statistics.mean(r['submits'] for r in rows),'meanFills':statistics.mean(r['fills'] for r in rows),'maxWin':max(r['pnl'] for r in rows),'maxLoss':min(r['pnl'] for r in rows),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rows),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rows),'meanReexpandBlocks':statistics.mean(r['reexpandBlocks'] for r in rows),'meanReexpandLocks':statistics.mean(r['reexpandLocks'] for r in rows),'meanRepairUnlocks':statistics.mean(r['repairUnlocks'] for r in rows)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_cycle_reexpand_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=CycleGuardSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
            try:r=run_student(sim,models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner'],'targetPnl':cr.get('targetPnl'),'targetBuy':cr.get('targetBuy')});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        s=agg(rows);out={'version':'ETH_CYCLE_REENTRY_REPAIR_BEFORE_REEXPAND_V1','researchOnly':True,'liveMutation':False,'boundary':['Base = frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation','Only new rule: when a material fill increases abs-net, remember the resulting dominant side and block new responsibility on that same side until a later material fill reduces abs-net','No static abs-net cap, no threshold sweep, no Target trajectory/objective/winner at runtime','Fresh101 is consumed development evidence only; <=180s no new exposure'],'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
