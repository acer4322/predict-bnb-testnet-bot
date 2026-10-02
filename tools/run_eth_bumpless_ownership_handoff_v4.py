from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
import run_eth_bumpless_linear_reexpand_blend_v1 as bl
EPS=1e-9
MODES=('LOCAL_HANDOFF','LINEAR_HANDOFF')

class OwnershipMixin:
    def init_ownership(self):
        self.intent={};self.handoff=set();self.handoffCount=0;self.handoffBlocks=0
    def effective_side(self,side):
        return float(self.inv[side])+float(self.reserved_authoritative(side))
    def role_now_effective(self,side):
        u=self.effective_side('UP');d=self.effective_side('DOWN')
        weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
        return 'REPAIR' if weak is not None and side==weak else 'EXPAND'
    def note_submit(self,n,side,t):
        self.intent[int(n)]={'role':self.role_now_effective(side),'side':side,'submitT':int(t)}
    def refresh_handoffs(self):
        for o in self.orders.values():
            n=int(o['n']);meta=self.intent.get(n)
            if not meta or meta['role']!='REPAIR' or n in self.handoff:continue
            s=self.snap(o)
            if not v1.live(s.get('status')):continue
            if self.role_now_effective(o['side'])=='EXPAND':
                self.handoff.add(n);self.handoffCount+=1
    def live_handoff_expansion(self):
        for o in self.orders.values():
            n=int(o['n'])
            if n not in self.handoff:continue
            s=self.snap(o)
            if v1.live(s.get('status')) and max(0.,float(s.get('leavesQty') or 0.))>EPS:return True
        return False
    def would_expand_effective(self,side,qty):
        u=self.effective_side('UP');d=self.effective_side('DOWN');pre=abs(u-d)
        if side=='UP':u+=float(qty)
        else:d+=float(qty)
        return abs(u-d)>pre+EPS

class LocalHandoffSim(OwnershipMixin,lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models):
        super().__init__(tape,mode,models);self.init_ownership()
    def process(self,t):
        super().process(t);self.refresh_handoffs()
    def submit(self,t,side,p,q):
        n0=int(self.n);role=self.role_now_effective(side)
        ok=super().submit(t,side,p,q)
        if ok is not False and int(self.n)>n0:self.intent[n0]={'role':role,'side':side,'submitT':int(t)}
        return ok
    def run_student_handoff(self,models,winner):
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
            if self.would_expand_effective(side,qty) and self.live_handoff_expansion():
                self.handoffBlocks+=1;continue
            self.submit(t,side,p,qty)
        return finish(self,winner)

class LinearHandoffSim(OwnershipMixin,bl.BumplessBlendSim):
    def __init__(self,tape,mode,models):
        super().__init__(tape,mode,models);self.init_ownership()
    def process(self,t):
        super().process(t);self.refresh_handoffs()
    def submit(self,t,side,p,q):
        n0=int(self.n);role=self.role_now_effective(side)
        ok=super().submit(t,side,p,q)
        if ok is not False and int(self.n)>n0:self.intent[n0]={'role':role,'side':side,'submitT':int(t)}
        return ok
    def run_student_handoff(self,models,winner):
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
            ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN';desired=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);legal=1/p if p>0 else 1e9;desired=max(desired,legal);desired=min(desired,12.);sent=desired
            expanding=self.would_expand_effective(side,desired)
            if expanding and self.live_handoff_expansion():self.handoffBlocks+=1;continue
            if self.expDebt>EPS and expanding:
                alpha=self.repair_progress();self.blendAttempts+=1;self.alphaHist.append(alpha);self.desiredQtyHist.append(desired);sent=desired*alpha
                if sent+EPS<legal:self.blendHeldBelowMin+=1;self.sentQtyHist.append(0.0);continue
                sent=min(sent,12.);self.blendSubmits+=1;self.sentQtyHist.append(sent)
            self.submit(t,side,p,sent)
        return finish(self,winner)

def finish(sim,winner):
    end2=int(sim.meta['lastReceivedMs']);v1.ex.advance_to(sim.bt,end2);sim.process(end2);pnl=sim.inv.get(str(winner).upper(),0.)-sim.cost;gross=sum(sim.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
    return {'pnl':pnl,'oppositePnl':sim.inv[opp]-sim.cost,'buyNotional':sim.cost,'pairCoverage':2*min(sim.inv.values())/gross if gross>EPS else 0.,'floor':min(sim.inv.values())-sim.cost,'absNet':abs(sim.inv['UP']-sim.inv['DOWN']),'submits':sim.submits,'fills':sim.fills,'handoffCount':sim.handoffCount,'handoffBlocks':sim.handoffBlocks,'blendAttempts':getattr(sim,'blendAttempts',0),'blendHeldBelowMin':getattr(sim,'blendHeldBelowMin',0)}

def agg(rs):
    p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs)
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>EPS for r in rs),'pnl':p,'buyNotional':b,'roi':p/b if b else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanHandoffs':statistics.mean(r['handoffCount'] for r in rs),'meanHandoffBlocks':statistics.mean(r['handoffBlocks'] for r in rs),'meanBlendAttempts':statistics.mean(r['blendAttempts'] for r in rs),'meanBlendHeldBelowMin':statistics.mean(r['blendHeldBelowMin'] for r in rs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--mode',required=True,choices=MODES);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_ownership_handoff_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        cls=LocalHandoffSim if a.mode=='LOCAL_HANDOFF' else LinearHandoffSim
        for i,cr in enumerate(test,1):
            sim=cls(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
            try:r=sim.run_student_handoff(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'mode':a.mode,'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows),'handoffs':sum(x['handoffCount'] for x in rows)}),flush=True)
        s=agg(rows);out={'version':'ETH_BUMPLESS_OWNERSHIP_HANDOFF_V4','mode':a.mode,'boundary':['No Target runtime threshold/classifier; Target used only as architecture reference','Live carrier admitted as REPAIR is not cancelled if it becomes EXPAND under authoritative effective inventory; its ownership is handed to EXPAND while preserving the same live actuator/order','While a handed-off expansion carrier is still live, no additional expansion responsibility is admitted','LOCAL_HANDOFF base=Local Pending Reservation; LINEAR_HANDOFF base=Bumpless Linear V1 own-state blending','No cooldown/threshold sweep; <=180s no new exposure; Fresh101 development-only'],'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'mode':a.mode,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
