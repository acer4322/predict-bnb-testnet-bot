from __future__ import annotations
import argparse,json,statistics,tempfile,zipfile,shutil,sys
from pathlib import Path
import numpy as np,joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1

EPS=1e-9
TERMINAL={'FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED'}

class PersistentCarrierSim(v3.RepairLedgerSim):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.carrierLedger={}
        self.ambiguousOwnershipBlocks=0
        self.incompatibleOwnershipBlocks=0
        self.excessCarrierCancelRequests=0
        self.maxOwnedRepairOverGap=0.0
        self.overOwnedSubmitViolations=0
    def _ledger_remaining(self,e):
        if e.get('terminalConfirmed'):return 0.0
        return max(0.0,float(e['submittedQty'])-float(e.get('actualFilled',0.0)))
    def _refresh_carrier_ledger(self,t):
        for key,e in self.carrierLedger.items():
            o=self.orders.get(key)
            if o is None:continue
            e['actualFilled']=max(float(e.get('actualFilled',0.0)),float(o.get('cum') or 0.0))
            try:s=self.snap(o)
            except Exception:s={}
            st=s.get('status');e['lastStatus']=st;e['lastSeenAt']=int(t)
            if key in self.cancelRequestedAt:e['cancelRequested']=True
            if st is not None and str(st).upper() in TERMINAL:e['terminalConfirmed']=True;e['terminalStatus']=str(st).upper();e['terminalAt']=int(t)
    def unresolved(self,side=None,objective_id=None):
        out=[]
        for key,e in self.carrierLedger.items():
            rem=self._ledger_remaining(e)
            if rem<=EPS:continue
            if side is not None and e.get('side')!=side:continue
            if objective_id is not None and e.get('objectiveId')!=objective_id:continue
            out.append((key,e,rem))
        return out
    def reserved_authoritative(self,side):
        return sum(rem for _,_,rem in self.unresolved(side=side))
    def outstanding_total(self):
        return sum(rem for _,_,rem in self.unresolved())
    def submit(self,t,side,p,q):
        # Measure pre-submit repair capacity using persistent ownership, not the current snapshot.
        ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None
        gap=abs(ai['UP']-ai['DOWN']) if weak else 0.0
        pre_owned=self.reserved_authoritative(side)
        n_before=self.n;pending_role=getattr(self,'_pendingAuthorizedRole',None);pending_oid=getattr(self,'_pendingAuthorizedObjectiveId',None)
        ok=super().submit(t,side,p,q)
        if not ok:return False
        key=f'{side}_{n_before}'
        self.carrierLedger[key]={
            'key':key,'side':side,'objectiveId':pending_oid,'objectiveRole':pending_role,
            'submittedQty':float(q),'actualFilled':0.0,'submittedAt':int(t),
            'cancelRequested':False,'terminalConfirmed':False,'lastStatus':None,'lastSeenAt':int(t)
        }
        if pending_role=='REPAIR' and weak==side and pre_owned+float(q)>gap+1e-7:self.overOwnedSubmitViolations+=1
        return True
    def _cancel_key(self,t,key):
        o=self.orders.get(key)
        if o is None:return False
        try:s=self.snap(o)
        except Exception:return False
        st=s.get('status')
        if st is not None and str(st).upper() in TERMINAL:return False
        cur=self.bt.orders(0).get(o['n'])
        if cur is None or not bool(cur.cancellable):return False
        self.cancelRequestedAt.setdefault(key,int(t))
        if key in self.carrierLedger:self.carrierLedger[key]['cancelRequested']=True
        try:self.bt.cancel(0,o['n'],False);return True
        except Exception:return False
    def _continuous_capacity_reconcile(self,t):
        self._refresh_carrier_ledger(t)
        obj=self.activeObjective
        if not obj or obj.get('role')!='REPAIR':return
        ai=self.auth_inv();side=obj.get('side');opp='DOWN' if side=='UP' else 'UP'
        if side is None:return
        gap=max(0.0,float(ai[opp])-float(ai[side]))
        rows=self.unresolved(side=side,objective_id=obj.get('id'))
        owned=sum(rem for _,_,rem in rows)
        self.maxOwnedRepairOverGap=max(self.maxOwnedRepairOverGap,max(0.0,owned-gap))
        if owned<=gap+EPS:return
        # Cancel newest carriers first until potential future fills no longer exceed the authoritative gap.
        excess=owned-gap
        for key,e,rem in sorted(rows,key=lambda x:int(x[1].get('submittedAt',0)),reverse=True):
            if excess<=EPS:break
            if e.get('cancelRequested'):continue
            if self._cancel_key(t,key):
                self.excessCarrierCancelRequests+=1
                excess-=rem
    def process(self,t):
        super().process(t)
        self._refresh_carrier_ledger(t)
        self._continuous_capacity_reconcile(t)
    def _incompatible_unresolved(self,objective_id,side):
        return [(k,e,r) for k,e,r in self.unresolved() if e.get('objectiveId')!=objective_id or e.get('side')!=side]
    def choose_authorized(self,t,end,proposed_side,proposed_qty):
        cur,seq,mask,debt,lag=self.lifecycle_features(t,end);p=self.lifecycle.predict(cur,seq,mask,debt,lag);self.lifecyclePredictions.append(p)
        self._refresh_carrier_ledger(t);self._reconcile_objective(t);self._continuous_capacity_reconcile(t)
        ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
        desired_role='EXPAND' if weak is None else ('REPAIR' if p['repair']>=0.5 else 'EXPAND')
        if self.activeObjective is None:
            # Do not start a new objective while any prior carrier can still materialize.
            if self.outstanding_total()>EPS:
                self.incompatibleOwnershipBlocks+=1;return None
            if desired_role=='EXPAND' and self.awaitingReentry:
                if p['reentry']<0.5:self.reauthBlocks+=1;return None
                self.awaitingReentry=False
            self.activeObjective=self._new_objective(desired_role,weak if desired_role=='REPAIR' else dom)
        elif self.activeObjective['role']!=desired_role:
            allow=False
            if self.activeObjective['role']=='REPAIR' and desired_role=='EXPAND':allow=(p['switch']>=0.5 and p['reentry']>=0.5)
            elif self.activeObjective['role']=='EXPAND' and desired_role=='REPAIR':allow=(p['switch']>=0.5 and p['repair']>=0.5)
            if allow:
                if self.outstanding_total()>EPS:
                    self.incompatibleOwnershipBlocks+=1;return None
                self.objectiveSwitches+=1;self.activeObjective=self._new_objective(desired_role,weak if desired_role=='REPAIR' else dom)
            else:
                self.reauthBlocks+=1;desired_role=self.activeObjective['role']
        side=weak if desired_role=='REPAIR' else dom
        if side is None:return None
        self.activeObjective['side']=side;oid=self.activeObjective.get('id')
        incompatible=self._incompatible_unresolved(oid,side)
        if incompatible:
            self.incompatibleOwnershipBlocks+=1;return None
        qty=float(proposed_qty)
        if desired_role=='REPAIR':
            gap=max(0.0,abs(float(ai['UP'])-float(ai['DOWN'])))
            owned=sum(rem for _,_,rem in self.unresolved(side=side,objective_id=oid))
            remaining=max(0.0,gap-owned)
            if remaining<=EPS:
                self.ambiguousOwnershipBlocks+=1;return None
            qty=min(qty,remaining)
        elif self.outstanding_total()>EPS:
            self.incompatibleOwnershipBlocks+=1;return None
        return side,qty,p,desired_role,oid
    def run_exam_v2(self,models,winner):
        r=super().run_exam_v2(models,winner)
        self._refresh_carrier_ledger(int(self.meta['lastReceivedMs']))
        r.update({
            'carrierLedgerEntries':len(self.carrierLedger),
            'unresolvedCarrierCount':len(self.unresolved()),
            'unresolvedCarrierQty':self.outstanding_total(),
            'ambiguousOwnershipBlocks':self.ambiguousOwnershipBlocks,
            'incompatibleOwnershipBlocks':self.incompatibleOwnershipBlocks,
            'excessCarrierCancelRequests':self.excessCarrierCancelRequests,
            'maxOwnedRepairOverGap':self.maxOwnedRepairOverGap,
            'overOwnedSubmitViolations':self.overOwnedSubmitViolations,
        })
        return r

def aggregate(rows):
    base=v3.aggregate(rows)
    def sm(k):return sum(float(r.get(k) or 0) for r in rows)
    base.update({
        'carrierLedgerEntries':int(sm('carrierLedgerEntries')),
        'unresolvedCarrierCount':int(sm('unresolvedCarrierCount')),
        'unresolvedCarrierQty':sm('unresolvedCarrierQty'),
        'ambiguousOwnershipBlocks':int(sm('ambiguousOwnershipBlocks')),
        'incompatibleOwnershipBlocks':int(sm('incompatibleOwnershipBlocks')),
        'excessCarrierCancelRequests':int(sm('excessCarrierCancelRequests')),
        'maxOwnedRepairOverGap':max((float(r.get('maxOwnedRepairOverGap') or 0) for r in rows),default=0.0),
        'overOwnedSubmitViolations':int(sm('overOwnedSubmitViolations')),
    });return base

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--output',required=True);ap.add_argument('--markets',type=int,default=24);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_repair_exam_v4_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,off1,off2=joblib.load(a.dagger_cache);print(json.dumps({'daggerCache':'loaded'}),flush=True)
        life=v3.LifecycleRuntime(Path(a.lifecycle_model));test=[r for r in cohort if r['split']!='TRAIN40'];n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx]
        summaries={};allrows=[]
        for scenario,cfg in ex1.SCENARIOS.items():
            rows=[]
            for i,cr in enumerate(selected,1):
                sim=PersistentCarrierSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'])
                try:r=sim.run_exam_v2(models,cr['winner'])
                finally:sim.close()
                r.update({'scenario':scenario,'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});rows.append(r);allrows.append(r)
                if i%8==0:print(json.dumps({'scenario':scenario,'progress':i,'repairToExpand':sum(x['repairToExpandAtFirstFill'] for x in rows),'overOwned':sum(x['overOwnedSubmitViolations'] for x in rows),'unresolvedQty':sum(x['unresolvedCarrierQty'] for x in rows)}),flush=True)
            summaries[scenario]=aggregate(rows)
        control=summaries['CONTROL'];gates={
            'controlNoSilentRepairToExpand':control['repairToExpandAtFirstFill']==0,
            'allScenariosNoAuthorizedTruthRoleMismatch':all(s['authorizedSubmitWithTruthRoleMismatch']==0 for s in summaries.values()),
            'allScenariosNoOverOwnedRepairSubmit':all(s['overOwnedSubmitViolations']==0 for s in summaries.values()),
            'allScenariosNoSubmitOnUnobservedSameSideFill':all(s['acceptedSubmitWhileSameSideUnobservedFill']==0 for s in summaries.values()),
            'objectiveLifecycleCanComplete':control['objectiveCompletions']>0,
            'ackLagDoesNotCollapseActivity':summaries['ACK_RELEASE_3000']['meanSubmits']>=0.5*control['meanSubmits'],
            'compoundDoesNotCollapseActivity':summaries['COMPOUND_3000']['meanSubmits']>=0.5*control['meanSubmits'],
        }
        out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V4_PERSISTENT_CARRIER_LEDGER','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed Fresh101 24-market functional-development cohort','selectedMarketIds':[int(r['marketId']) for r in selected],'architecture':['V3 lifecycle GRU + debt residual semantics','persistent per-carrier ownership independent of snapshot visibility','NONE/missing never releases responsibility','cancel-inflight remains owned until terminal','aggregate objective remaining responsibility = authoritative gap - unresolved same-objective carrier remainder','incompatible unresolved carriers block objective handoff','continuous excess-carrier cancellation'], 'round1Offline':off1,'round2Offline':off2,'scenarios':summaries,'gates':gates,'allPass':all(gates.values()),'rows':allrows,'boundary':['No dream fills','No BTC runtime labels/thresholds/quantities/gradients','Fixed delay scenarios are diagnostics, not proof of learned repair','Consumed development evidence only','If pass, randomized delay-agnostic disturbance exam is required before any new-market promotion claim']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'gates':gates,'scenarios':summaries},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
