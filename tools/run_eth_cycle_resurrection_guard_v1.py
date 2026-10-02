from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp


def weak_side(inv):
    u=float(inv['UP']);d=float(inv['DOWN'])
    if u<d-v1.EPS:return 'UP'
    if d<u-v1.EPS:return 'DOWN'
    return None

def role_for(side,inv):
    w=weak_side(inv)
    if w is None:return 'BALANCED'
    return 'REPAIR' if side==w else 'DOMINANT'

class GuardSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models,policy):
        super().__init__(tape,mode,models);self.policy=policy;self.guard={};self.guardBlocks=0;self.guardArms=0;self.guardHoldResets=0;self.guardOppResets=0
    def should_arm(self,role):
        if self.policy=='DOMBAL_GUARD':return role in {'DOMINANT','BALANCED'}
        if self.policy=='ALL_GUARD':return True
        return False
    def process(self,t):
        pre={s:float(self.reserved_authoritative(s)) for s in ('UP','DOWN')}
        super().process(t)
        post={s:float(self.reserved_authoritative(s)) for s in ('UP','DOWN')}
        for side in ('UP','DOWN'):
            if pre[side]>v1.EPS and post[side]<=v1.EPS:
                role=role_for(side,self.inv)
                if self.should_arm(role):
                    self.guard[side]={'role':role,'armedAt':int(t)};self.guardArms+=1
    def note_hold(self):
        if self.guard:
            self.guardHoldResets+=len(self.guard);self.guard.clear()
    def allow_submit(self,side):
        if side in self.guard:
            self.guardBlocks+=1;return False
        return True
    def note_accepted(self,side):
        opp='DOWN' if side=='UP' else 'UP'
        if opp in self.guard:
            self.guardOppResets+=1;self.guard.pop(opp,None)


def run_policy(sim,models,winner):
    ups=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);v1.ex.advance_to(sim.bt,first)
    end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
    for u in ups:
        t=int(u[1]);v1.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);ca=v1.apply(sim.book,u);qv=v1.quotes(sim.book)
        if not qv:continue
        if sim.firstValid is None:sim.firstValid=t
        if (end-t)/1000.<=180:continue
        sim.seed_if_needed(t,qv)
        if not sim.seeded:continue
        x=sim.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
        if pa<models['actionTh']:
            sim.note_hold();continue
        ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN'
        if not sim.allow_submit(side):continue
        p=float(qv[side]['bid']);qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));qty=max(qty,1/p);qty=min(qty,12.)
        if sim.submit(t,side,p,qty):sim.note_accepted(side)
    end2=int(sim.meta['lastReceivedMs']);v1.ex.advance_to(sim.bt,end2);sim.process(end2)
    win=str(winner).upper();opp='UP' if win=='DOWN' else 'DOWN';pnl=float(sim.inv[win]-sim.cost);op=float(sim.inv[opp]-sim.cost);gross=sum(sim.inv.values())
    return {'pnl':pnl,'oppositePnl':op,'buyNotional':float(sim.cost),'pairCoverage':2*min(sim.inv.values())/gross if gross>v1.EPS else 0.0,'floor':min(sim.inv.values())-sim.cost,'absNet':abs(sim.inv['UP']-sim.inv['DOWN']),'submits':sim.submits,'fills':sim.fills,'up':sim.inv['UP'],'down':sim.inv['DOWN'],'guardArms':sim.guardArms,'guardBlocks':sim.guardBlocks,'guardHoldResets':sim.guardHoldResets,'guardOppResets':sim.guardOppResets}

def agg(rows):
    buy=sum(r['buyNotional'] for r in rows);p=sum(r['pnl'] for r in rows)
    return {'markets':len(rows),'activeMarkets':sum(r['buyNotional']>v1.EPS for r in rows),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'winRate':sum(r['pnl']>0 for r in rows)/len(rows),'meanBuy':statistics.mean(r['buyNotional'] for r in rows),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rows),'positiveFloorRate':sum(r['floor']>=0 for r in rows)/len(rows),'meanAbsNet':statistics.mean(r['absNet'] for r in rows),'meanSubmits':statistics.mean(r['submits'] for r in rows),'meanFills':statistics.mean(r['fills'] for r in rows),'maxWin':max(r['pnl'] for r in rows),'maxLoss':min(r['pnl'] for r in rows),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rows),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rows),'meanGuardArms':statistics.mean(r['guardArms'] for r in rows),'meanGuardBlocks':statistics.mean(r['guardBlocks'] for r in rows),'meanGuardHoldResets':statistics.mean(r['guardHoldResets'] for r in rows),'meanGuardOppResets':statistics.mean(r['guardOppResets'] for r in rows)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_cycle_guard_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for policy in ('DOMBAL_GUARD','ALL_GUARD'):
            pr=[]
            for i,cr in enumerate(test,1):
                sim=GuardSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,policy)
                try:r=run_policy(sim,models,cr['winner'])
                finally:sim.close()
                r.update({'marketId':int(cr['marketId']),'policy':policy,'winner':cr['winner'],'targetPnl':cr.get('targetPnl'),'targetBuy':cr.get('targetBuy')});pr.append(r);rows.append(r)
                if i%20==0:print(json.dumps({'policy':policy,'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in pr),'winsSoFar':sum(x['pnl']>0 for x in pr)}),flush=True)
        summaries=[]
        for policy in ('DOMBAL_GUARD','ALL_GUARD'):
            s=agg([r for r in rows if r['policy']==policy]);s['policy']=policy;summaries.append(s)
        out={'version':'ETH_CYCLE_RESURRECTION_GUARD_V1','researchOnly':True,'liveMutation':False,'boundary':['Base frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation','Guard arms only when an authoritative same-side carrier disappears','DOMBAL_GUARD arms only if released carrier side is currently DOMINANT or BALANCED; REPAIR release remains unmodified','ALL_GUARD arms on every release','Armed same-side successor is blocked until the frozen base student emits at least one HOLD checkpoint, or an accepted opposite-side responsibility clears the old identity','No time threshold, no Target runtime data, no winner/outcome in decisions, <=180s no new exposure','Fresh101 is already-consumed development evidence only; no promotion from this cohort'],'round1Offline':off1,'round2Offline':off2,'summaries':summaries,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summaries':summaries},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
