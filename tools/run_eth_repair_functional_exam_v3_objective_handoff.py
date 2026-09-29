from __future__ import annotations
import argparse, json, math, statistics, tempfile, zipfile, shutil, sys
from collections import deque
from pathlib import Path
import numpy as np
import torch
import joblib

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
TOOLS=ROOT/'tools'
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))

import run_eth_repair_functional_exam_v1 as ex1
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp
import train_eth_observation_debt_lifecycle_memory_v1 as od1
import train_eth_observation_debt_residual_adapter_v2 as od2

EPS=1e-9


def state_metrics(inv,cost):
    up=float(inv['UP']);dn=float(inv['DOWN']);gross=up+dn;pair=min(up,dn);gap=abs(up-dn)
    return {'gross':gross,'pair':pair,'gap':gap,'pc':2*pair/gross if gross>EPS else 1.0,'ab':gap/gross if gross>EPS else 0.0,'fr':(pair-cost)/max(cost,1.0)}

class LifecycleRuntime:
    def __init__(self, model_path:Path):
        ck=torch.load(model_path,map_location='cpu',weights_only=False)
        self.mu=np.asarray(ck['mu'],np.float32);self.sd=np.asarray(ck['sd'],np.float32)
        self.tmu=np.asarray(ck['tmu'],np.float32);self.tsd=np.asarray(ck['tsd'],np.float32)
        self.dmu=np.asarray(ck['dmu'],np.float32);self.dsd=np.asarray(ck['dsd'],np.float32)
        self.base=od1.Memory();self.base.load_state_dict(ck['base_state_dict']);self.base.eval()
        self.adapter=od2.ResidualAdapter(True);self.adapter.load_state_dict(ck['debt_residual_state_dict']);self.adapter.eval()
    def predict(self,cur,seq,mask,debt,lag_count):
        cz=((np.asarray(cur,np.float32)-self.mu)/self.sd).reshape(1,-1)
        sz=((np.asarray(seq,np.float32)-self.tmu)/self.tsd)*np.asarray(mask,np.float32)[:,None]
        dz=((np.asarray(debt,np.float32)-self.dmu)/self.dsd).reshape(1,-1)
        lag=np.asarray([min(max(lag_count,0),od1.MAX_LAG)/od1.MAX_LAG],np.float32)
        with torch.no_grad():
            ct=torch.from_numpy(cz);st=torch.from_numpy(sz[None,...]);mt=torch.from_numpy(np.asarray(mask,np.float32)[None,...]);dt=torch.from_numpy(dz);lt=torch.from_numpy(lag)
            out={}
            for task in od1.TASKS:
                bl=self.base(ct,st,mt,task);log=self.adapter(ct,bl,dt,lt,task);out[task]=float(torch.sigmoid(log).item())
        return out

class RepairLedgerSim(ex1.RepairExamSim):
    def __init__(self,tape,mode,models,lifecycle,fill_obs_lag_ms=0,ack_release_lag_ms=0):
        super().__init__(tape,mode,models,fill_obs_lag_ms,ack_release_lag_ms)
        self.lifecycle=lifecycle
        self.truthSideCost={'UP':0.0,'DOWN':0.0}
        self.authHist=[]
        self.visibleAuthHist=[]
        self.activeObjective=None
        self.nextObjectiveId=1
        self.awaitingReentry=False
        self.submitRoleAuthorized={}
        self.authorizedSubmitWithTruthRoleMismatch=0
        self.objectiveSwitches=0;self.objectiveCompletions=0;self.objectiveInvalidations=0;self.reauthBlocks=0;self.remainingCapBlocks=0;self.globalOwnershipBlocks=0
        self.lifecyclePredictions=[]
    def auth_inv(self):
        return {'UP':float(self.inv['UP'])+sum(float(x['qty']) for x in self.pendingObs if x['side']=='UP'),
                'DOWN':float(self.inv['DOWN'])+sum(float(x['qty']) for x in self.pendingObs if x['side']=='DOWN')}
    def auth_cost(self):
        return float(self.cost)+sum(float(x['qty'])*float(x['price']) for x in self.pendingObs)
    def reserved_authoritative(self,side):
        # One responsibility, one count: local pre-ack and venue-live are two views of the same order.
        total=0.0
        for key,o in self.orders.items():
            if o.get('side')!=side:continue
            snap=self.snap(o);status=snap.get('status')
            if v1.live(status):
                total+=max(0.0,float(snap.get('leavesQty') or 0.0))
            elif status is None and key in self.localPending[side]:
                total+=max(0.0,float(self.localPending[side][key].get('remaining',0.0)))
        return total
    def _new_objective(self,role,side):
        obj={'id':self.nextObjectiveId,'role':role,'side':side};self.nextObjectiveId+=1;return obj
    def _cancel_objective_carriers(self,t,objective_id):
        for key,o in self.orders.items():
            if o.get('objective_id')!=objective_id:continue
            snap=self.snap(o)
            if not v1.live(snap.get('status')):continue
            cur=self.bt.orders(0).get(o['n'])
            if cur is not None and bool(cur.cancellable):
                self.cancelRequestedAt.setdefault(key,int(t))
                try:self.bt.cancel(0,o['n'],False)
                except Exception:pass
    def _reconcile_objective(self,t):
        obj=self.activeObjective
        if not obj or obj.get('role')!='REPAIR':return
        ai=self.auth_inv();side=obj.get('side');opp='DOWN' if side=='UP' else 'UP'
        # Structural completion/invalidation: the side this objective was repairing is no longer weak.
        if side is None or ai[side]>=ai[opp]-EPS:
            oid=obj['id'];self.objectiveCompletions+=1
            if ai[side]>ai[opp]+EPS:self.objectiveInvalidations+=1
            self.activeObjective=None;self.awaitingReentry=True
            self._cancel_objective_carriers(t,oid)
    def submit(self,t,side,p,q):
        n_before=self.n
        truth_role=ex1.role_from_inv(self.truthInv,side)
        auth_role=getattr(self,'_pendingAuthorizedRole',None) or truth_role
        auth_oid=getattr(self,'_pendingAuthorizedObjectiveId',None)
        ok=super().submit(t,side,p,q)
        if not ok:return False
        key=f'{side}_{n_before}'
        self.submitRoleAuthorized[key]=auth_role
        if key in self.orders:
            self.orders[key]['objective_role']=auth_role;self.orders[key]['objective_id']=auth_oid
        if auth_role!=truth_role:self.authorizedSubmitWithTruthRoleMismatch+=1
        self._pendingAuthorizedRole=None;self._pendingAuthorizedObjectiveId=None
        return True
    def _append_truth_fill_token(self,t,side,qty,price,pre_inv,pre_cost):
        pre=state_metrics(pre_inv,pre_cost);post=state_metrics(self.truthInv,self.truthCost)
        role=ex1.role_from_inv(pre_inv,side);rel=1 if role=='REPAIR' else -1
        prev_t=self.authHist[-1]['time'] if self.authHist else None
        elapsed=(int(t)-int(prev_t)) if prev_t is not None else 1e6
        token=np.asarray([float(rel),post['ab']-pre['ab'],post['pc']-pre['pc'],max(-5,min(5,post['fr']-pre['fr'])),math.log1p(min(elapsed,120000))/math.log1p(120000),max(0,min(3,float(qty)/max(pre['gap'],1.))),float(price),post['ab'],post['pc']],np.float32)
        self.authHist.append({'rel':rel,'time':int(t),'side':side,'shares':float(qty),'price':float(price),'token':token})
    def process(self,t):
        # Custom copy of exam fill processing so authoritative history is updated exactly at venue materialization.
        self._apply_due_observations(t)
        for key,o in self.orders.items():
            s=self.snap(o);status=s.get('status')
            if status is not None and key not in self.firstVisibleAt:self.firstVisibleAt[key]=int(t)
            cum=float(s.get('cumExecQty') or 0.0);inc=max(0.0,cum-float(o.get('cum') or 0.0))
            if inc>EPS:
                px=v1.fill_price(o['side'],s,o['price']);pre_inv=dict(self.truthInv);pre_cost=float(self.truthCost);pre_truth_role=ex1.role_from_inv(pre_inv,o['side'])
                self.truthInv[o['side']]+=inc;self.truthCost+=inc*px;self.truthSideCost[o['side']]+=inc*px
                self._append_truth_fill_token(t,o['side'],inc,px,pre_inv,pre_cost)
                self.actualFillEvents+=1
                if status=='PARTIALLY_FILLED' or cum<float(o.get('qty') or 0.0)-EPS:self.partialFillEvents+=1
                if key in self.cancelRequestedAt:self.lateFillAfterCancelEvents+=1
                if key not in self.firstFillSeen:
                    self.firstFillSeen.add(key);submit_role=self.submitRoleAuthorized.get(key,self.submitRoleObserved.get(key))
                    if submit_role==pre_truth_role:self.roleStableAtFill+=1
                    elif submit_role=='REPAIR' and pre_truth_role=='EXPAND':self.repairToExpandAtFill+=1
                    elif submit_role=='EXPAND' and pre_truth_role=='REPAIR':self.expandToRepairAtFill+=1
                self.pendingObs.append({'due':int(t)+self.fillObsLagMs,'side':o['side'],'qty':inc,'price':px,'key':key})
                self.maxUnobservedFillQty=max(self.maxUnobservedFillQty,self.unobserved_qty());o['cum']=cum
            o['status']=status
        for side in ('UP','DOWN'):
            for key in list(self.localPending[side]):
                o=self.orders.get(key)
                if o is None:self.localPending[side].pop(key,None);continue
                s=self.snap(o);status=s.get('status')
                if status is None:continue
                vis=int(self.firstVisibleAt.get(key,t))
                if int(t)-vis>=self.ackReleaseLagMs:self.localPending[side].pop(key,None)
        self._apply_due_observations(t)
        self._reconcile_objective(t)
    def _visible_history(self,t):
        # An actual fill becomes policy-visible only when its observation delay has elapsed.
        cutoff=int(t)-self.fillObsLagMs
        return [m for m in self.authHist if int(m['time'])<=cutoff]
    def lifecycle_features(self,t,end):
        vis=self._visible_history(t);auth=self.authHist
        ou=float(self.inv['UP']);od=float(self.inv['DOWN']);oc=float(self.cost);gross=ou+od;pair=min(ou,od);gap=abs(ou-od);pc=2*pair/gross if gross>EPS else 1.;ab=gap/gross if gross>EPS else 0.;fr=(pair-oc)/max(oc,1.);br=(max(ou,od)-oc)/max(oc,1.)
        avgup=float(self.sideCost['UP'])/ou if ou>EPS else 0.;avgdn=float(self.sideCost['DOWN'])/od if od>EPS else 0.;prev=vis[-1]['rel'] if vis else 0;age=(int(t)-int(vis[-1]['time'])) if vis else 1e6
        cur=np.asarray([max(-30,min(330,(end-t)/1000))/300,pc,ab,max(-5,min(5,fr)),max(-5,min(5,br)),0.,float(prev),math.log1p(min(age,120000))/math.log1p(120000),max(-1,min(1,avgup-avgdn)),math.log1p(gross)/math.log1p(500)],np.float32)
        seq=np.zeros((od1.SEQ,len(od1.TOK_FEATURES)),np.float32);mask=np.zeros(od1.SEQ,np.float32);vv=vis[-od1.SEQ:]
        if vv:seq[-len(vv):]=np.asarray([m['token'] for m in vv],np.float32);mask[-len(vv):]=1.
        ai=self.auth_inv();ac=self.auth_cost();tm=state_metrics(ai,ac);om=state_metrics(self.inv,self.cost);hidden=auth[len(vis):];hup=sum(m['shares'] for m in hidden if m['side']=='UP');hdn=sum(m['shares'] for m in hidden if m['side']=='DOWN');hcost=sum(m['shares']*m['price'] for m in hidden)
        active_rel=auth[-1]['rel'] if auth else 0;streak=0
        for m in reversed(auth):
            if m['rel']==active_rel:streak+=1
            else:break
        auth_age=(int(t)-int(auth[-1]['time'])) if auth else 1e6;repair=[m for m in auth if m['rel']==1];cra=sum(max(0.,-m['token'][1]) for m in repair);crp=sum(m['token'][2] for m in repair);crf=sum(m['token'][3] for m in repair);seg=auth[-streak:] if streak else [];segprog=sum((-m['token'][1] if active_rel==1 else m['token'][1]) for m in seg) if seg else 0.;tg=max(tm['gross'],1.);tc=max(ac,1.)
        debt=np.asarray([min(len(hidden),od1.MAX_LAG)/od1.MAX_LAG,(hup+hdn)/tg,(hup-hdn)/tg,hcost/tc,tm['ab']-om['ab'],tm['pc']-om['pc'],max(-5,min(5,tm['fr']-om['fr'])),float(active_rel),min(streak,12)/12.,math.log1p(min(auth_age,120000))/math.log1p(120000),min(cra,3.),max(-3,min(3,crp)),max(-3,min(3,crf)),max(-3,min(3,segprog))],np.float32)
        return cur,seq,mask,debt,len(hidden)
    def outstanding_total(self):
        return sum(self.reserved_authoritative(s) for s in ('UP','DOWN'))
    def choose_authorized(self,t,end,proposed_side,proposed_qty):
        cur,seq,mask,debt,lag=self.lifecycle_features(t,end);p=self.lifecycle.predict(cur,seq,mask,debt,lag);self.lifecyclePredictions.append(p)
        ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
        if weak is None:
            desired_role='EXPAND'
        else:
            desired_role='REPAIR' if p['repair']>=0.5 else 'EXPAND'
        # Persistent objective: completion is structural; a new EXPAND after completed REPAIR still needs learned re-entry authorization.
        self._reconcile_objective(t)
        if self.activeObjective is None:
            if desired_role=='EXPAND' and self.awaitingReentry:
                if p['reentry']<0.5:self.reauthBlocks+=1;return None
                self.awaitingReentry=False
            self.activeObjective=self._new_objective(desired_role,weak if desired_role=='REPAIR' else dom)
        elif self.activeObjective['role']!=desired_role:
            allow=False
            if self.activeObjective['role']=='REPAIR' and desired_role=='EXPAND':allow=(p['switch']>=0.5 and p['reentry']>=0.5)
            elif self.activeObjective['role']=='EXPAND' and desired_role=='REPAIR':allow=(p['switch']>=0.5 and p['repair']>=0.5)
            if allow:
                self.objectiveSwitches+=1;self.activeObjective=self._new_objective(desired_role,weak if desired_role=='REPAIR' else dom)
            else:self.reauthBlocks+=1;desired_role=self.activeObjective['role']
        # Recompute side from authoritative state, never from stale observed imbalance.
        if desired_role=='REPAIR':side=weak
        else:side=dom
        if side is None:return None
        self.activeObjective['side']=side
        # Equivalent live/local/debt responsibility owns the objective; do not recreate it.
        if self.reserved_authoritative(side)>EPS or self.unobserved_qty(side)>EPS:
            self.globalOwnershipBlocks+=1;return None
        qty=float(proposed_qty)
        if desired_role=='REPAIR':
            gap=abs(ai['UP']-ai['DOWN'])
            # Remaining repair responsibility is authoritative gap; cap carrier so it cannot cross by itself.
            qty=min(qty,gap)
            if qty<=EPS:self._reconcile_objective(t);self.remainingCapBlocks+=1;return None
        return side,qty,p,desired_role,self.activeObjective.get('id') if self.activeObjective else None
    def run_exam_v2(self,models,winner):
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
            ps=float(models['side'].predict_proba(x)[0,1]);prop='UP' if ps>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p0=float(qv[prop]['bid']);qty=max(qty,1/p0);qty=min(qty,12.)
            z=self.choose_authorized(t,end,prop,qty)
            if z is None:continue
            side,qty,_,authorized_role,objective_id=z;p=float(qv[side]['bid']);legal=1/p if p>EPS else 1e9
            # Legal min may exceed remaining repair responsibility; in that case wait rather than over-repair/cross.
            if self.activeObjective and self.activeObjective['role']=='REPAIR' and qty<legal:self.remainingCapBlocks+=1;continue
            qty=max(qty,legal);qty=min(qty,12.)
            self._pendingAuthorizedRole=authorized_role;self._pendingAuthorizedObjectiveId=objective_id
            self.submit(t,side,p,qty)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);self._apply_due_observations(end2+self.fillObsLagMs+self.ackReleaseLagMs+1)
        base=super().run_exam(models,winner) if False else None
        gross=sum(self.inv.values());firstfills=self.roleStableAtFill+self.repairToExpandAtFill+self.expandToRepairAtFill
        return {'pnlDiagnosticOnly':self.inv.get(str(winner).upper(),0.)-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fillsObserved':self.fills,'actualFillEvents':self.actualFillEvents,'partialFillEvents':self.partialFillEvents,'lateFillAfterCancelEvents':self.lateFillAfterCancelEvents,'repairToExpandAtFirstFill':self.repairToExpandAtFill,'expandToRepairAtFirstFill':self.expandToRepairAtFill,'roleStableAtFirstFill':self.roleStableAtFill,'firstFillRoleEvents':firstfills,'repairToExpandRate':self.repairToExpandAtFill/firstfills if firstfills else 0.,'acceptedSubmitWhileSameSideUnobservedFill':self.acceptedSubmitWhileSameSideUnobservedFill,'acceptedSubmitWithObservedTruthRoleMismatch':self.acceptedSubmitWithObservedTruthRoleMismatch,'maxUnobservedFillQty':self.maxUnobservedFillQty,'obsDebtClears':self.obsDebtClears,'duplicateBlocked':self.duplicateBlocked,'localPendingBlocked':self.localPendingBlocked,'objectiveSwitches':self.objectiveSwitches,'objectiveCompletions':self.objectiveCompletions,'objectiveInvalidations':self.objectiveInvalidations,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'observedSubmitWithTruthRoleMismatchDiagnostic':self.acceptedSubmitWithObservedTruthRoleMismatch,'reauthBlocks':self.reauthBlocks,'globalOwnershipBlocks':self.globalOwnershipBlocks,'remainingCapBlocks':self.remainingCapBlocks}

def aggregate(rows):
    def sm(k):return sum(float(r.get(k) or 0) for r in rows)
    ff=sm('firstFillRoleEvents')
    return {'markets':len(rows),'activeMarkets':sum(float(r['buyNotional'])>EPS for r in rows),'meanBuy':statistics.mean(float(r['buyNotional']) for r in rows),'meanSubmits':statistics.mean(float(r['submits']) for r in rows),'meanAbsNet':statistics.mean(float(r['absNet']) for r in rows),'meanPairCoverage':statistics.mean(float(r['pairCoverage']) for r in rows),'actualFillEvents':int(sm('actualFillEvents')),'partialFillEvents':int(sm('partialFillEvents')),'lateFillAfterCancelEvents':int(sm('lateFillAfterCancelEvents')),'firstFillRoleEvents':int(ff),'repairToExpandAtFirstFill':int(sm('repairToExpandAtFirstFill')),'repairToExpandRate':sm('repairToExpandAtFirstFill')/ff if ff else None,'acceptedSubmitWhileSameSideUnobservedFill':int(sm('acceptedSubmitWhileSameSideUnobservedFill')),'acceptedSubmitWithObservedTruthRoleMismatch':int(sm('acceptedSubmitWithObservedTruthRoleMismatch')),'maxUnobservedFillQty':max(float(r['maxUnobservedFillQty']) for r in rows) if rows else 0.,'objectiveSwitches':int(sm('objectiveSwitches')),'objectiveCompletions':int(sm('objectiveCompletions')),'objectiveInvalidations':int(sm('objectiveInvalidations')),'authorizedSubmitWithTruthRoleMismatch':int(sm('authorizedSubmitWithTruthRoleMismatch')),'observedSubmitWithTruthRoleMismatchDiagnostic':int(sm('observedSubmitWithTruthRoleMismatchDiagnostic')),'reauthBlocks':int(sm('reauthBlocks')),'globalOwnershipBlocks':int(sm('globalOwnershipBlocks')),'remainingCapBlocks':int(sm('remainingCapBlocks')),'pnlDiagnosticOnly':sm('pnlDiagnosticOnly')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--output',required=True);ap.add_argument('--markets',type=int,default=24);ap.add_argument('--dagger-cache');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_repair_exam_v2_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));
        cache=Path(a.dagger_cache) if a.dagger_cache else None
        if cache and cache.exists():
            models,off1,off2=joblib.load(cache);print(json.dumps({'daggerCache':'loaded','path':str(cache)}),flush=True)
        else:
            models,off1,off2=lp.train_models(tmp,cohort,traj)
            if cache:
                cache.parent.mkdir(parents=True,exist_ok=True);joblib.dump((models,off1,off2),cache);print(json.dumps({'daggerCache':'saved','path':str(cache)}),flush=True)
        life=LifecycleRuntime(Path(a.lifecycle_model));test=[r for r in cohort if r['split']!='TRAIN40'];n=min(int(a.markets),len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx];allrows=[];summaries={}
        for scenario,cfg in ex1.SCENARIOS.items():
            rows=[]
            for i,cr in enumerate(selected,1):
                sim=RepairLedgerSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'])
                try:r=sim.run_exam_v2(models,cr['winner'])
                finally:sim.close()
                r.update({'scenario':scenario,'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});rows.append(r);allrows.append(r)
                if i%8==0:print(json.dumps({'scenario':scenario,'progress':i,'unsafe':sum(x['acceptedSubmitWhileSameSideUnobservedFill'] for x in rows),'roleMismatch':sum(x['acceptedSubmitWithObservedTruthRoleMismatch'] for x in rows),'repairToExpand':sum(x['repairToExpandAtFirstFill'] for x in rows)}),flush=True)
            summaries[scenario]=aggregate(rows)
        control=summaries['CONTROL'];gates={'controlNoSilentRepairToExpand':control['repairToExpandAtFirstFill']==0,'fillLag1000NoSubmitOnUnobservedSameSideFill':summaries['FILL_OBS_1000']['acceptedSubmitWhileSameSideUnobservedFill']==0,'fillLag3000NoSubmitOnUnobservedSameSideFill':summaries['FILL_OBS_3000']['acceptedSubmitWhileSameSideUnobservedFill']==0,'compoundNoSubmitOnUnobservedSameSideFill':summaries['COMPOUND_3000']['acceptedSubmitWhileSameSideUnobservedFill']==0,'allScenariosNoAuthorizedTruthRoleMismatch':all(s['authorizedSubmitWithTruthRoleMismatch']==0 for s in summaries.values()),'objectiveLifecycleCanComplete':control['objectiveCompletions']>0,'ackLagDoesNotCollapseActivity':summaries['ACK_RELEASE_3000']['meanSubmits']>=0.5*control['meanSubmits'],'compoundDoesNotCollapseActivity':summaries['COMPOUND_3000']['meanSubmits']>=0.5*control['meanSubmits']}
        out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V3_OBJECTIVE_HANDOFF','researchOnly':True,'performanceGraduationEligible':False,'cohort':'same consumed Fresh101 24-market functional-development cohort','selectedMarketIds':[int(r['marketId']) for r in selected],'architecture':['frozen ETH lifecycle GRU + debt residual adapter','authoritative observed+unabsorbed-fill inventory','persistent objective identity','live/local/debt ownership conservation','repair remaining-responsibility cap','learned switch/reentry reauthorization','deduplicated local/venue ownership','structural repair completion + carrier cancellation','authorized-role first-fill scoring'],'round1Offline':off1,'round2Offline':off2,'scenarios':summaries,'gates':gates,'allPass':all(gates.values()),'rows':allrows,'boundary':['No dream fills','No BTC runtime labels/thresholds/gradients','No winner/future PnL in gates','Consumed Fresh101 development-only','V2 DAgger cache reused unchanged; no retraining required when cache is present','Passing requires later frozen new-market HFT exam beyond 1823545']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'gates':gates,'scenarios':summaries},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
