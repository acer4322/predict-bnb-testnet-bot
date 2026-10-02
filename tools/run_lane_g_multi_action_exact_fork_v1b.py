from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3

v2=r264.v2; EPS=1e-9
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}
ACTIONS={'KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE'}

FROZEN={
  1946317:{'t':1788534350631,'generation':4,'scopeSide':'DOWN','repairSide':'UP','thesisSide':'UP',
    'obligationOutstanding':1.8181818181818181,'siblingKey':'UP_37','siblingPrice':0.47,'siblingRemaining':2.127659574468085,
    'debt':2.7664517696201454,'ordinary':{'side':'DOWN','price':0.51,'qty':1.9607843137254901,'risk':1.0},
    'composite':{'side':'UP','price':0.48,'qty':2.722125528485394,'unreservedDebt':0.6387921951520603,'venue':2.0833333333333335,'risk':1.0}},
  1946640:{'t':1788535906741,'generation':2,'scopeSide':'DOWN','repairSide':'UP','thesisSide':'UP',
    'obligationOutstanding':2.0,'siblingKey':'UP_34','siblingPrice':0.46,'siblingRemaining':2.1739130434782608,
    'debt':3.397306397306397,'ordinary':{'side':'DOWN','price':0.53,'qty':1.8867924528301885,'risk':1.0},
    'composite':{'side':'UP','price':0.42,'qty':3.6043457347805172,'unreservedDebt':1.2233933538281363,'venue':2.380952380952381,'risk':1.0}},
}

def close(a,b,tol=1e-8): return abs(float(a)-float(b))<=tol

class MultiActionExactForkSim(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,market_id:int,action:str,fanout_limit=1,max_slots=4):
        if action not in ACTIONS: raise ValueError(action)
        super().__init__(tape,fanout_limit,max_slots)
        self.marketId=int(market_id);self.action=action;self.spec=FROZEN[self.marketId]
        self.intentThesisSide=None
        self.forkTriggered=False;self.forkActive=False;self.forkResolved=False;self.forkTrigger=None;self.forkEvent=None
        self.forkBranchKey=None;self.forkSiblingKey=None;self.forkInitialCum={};self.forkExistingActiveCum={};self.forkActiveFillQtyAtTrigger=0.0
        self.forkManagerBlocks=Counter();self.triggerParityErrors=[];self.postEventFirstManagerAction=None
        self.r303=Counter();self.r303Events=[];self.r303Ledger=GenerationAwareSharedParentDebtAllocationLedgerV3()
        self.r303Parent=None;self.r303SharedKeys=set();self.r303BaseCum={};self.r303Authority=None;self.r303ExpectedByReceipt={}
        self.r303SplitMismatch=0;self.r303OverflowRisk=0.0;self.r303OverflowQty=0.0;self.r303RepairQty=0.0
        self.r303NewObligationQty=0.0;self.r303AuthorityOverrun=0.0

    def _payoff(self):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);cost=float(self.cost);best=max(u,d)-cost;floor=min(u,d)-cost
        return {'upQty':u,'downQty':d,'cost':cost,'best':best,'floor':floor,'gap':best-floor}

    def _live_status(self,key):
        o=self.orders.get(key)
        if not o:return {'status':'MISSING','cum':0.0,'live':False,'cancelRequested':False}
        try:s=self.snap(o);st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
        except Exception:st='';cum=float(o.get('cum') or 0.0)
        return {'status':st,'cum':cum,'live':st not in v2.TERMINAL_STATUSES,'cancelRequested':bool(o.get('cancelRequested'))}

    def _record_post_event_action(self,t,kind,detail):
        if self.forkResolved and self.postEventFirstManagerAction is None:
            ob=self._obligation_current();self.postEventFirstManagerAction={'t':int(t),'kind':kind,'detail':detail,'payoff':self._payoff(),'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,'repairProgressClocks':int(self.scopeRepairProgressClocks),'obligation':({k:ob.get(k) for k in ['outstanding','repaidQty','bornQty','closeReason']} if ob else None),'slots':len(self.slot_key),'active':len(self.activeKeys),'availableExpandCredit':float(self._available_expand_risk_credit())}

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before=self.n;ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='PROBE_CORE' and self.intentThesisSide is None:
            self.intentThesisSide=str(side)
        if ok:self._record_post_event_action(t,'PASSIVE_SUBMIT',{'side':str(side),'role':str(role),'price':float(p),'qty':float(q),'key':f'{side}_{before}'})
        return ok

    def _submit_active(self,t,side,role,q,score,diag):
        before=self.n;ok=super()._submit_active(t,side,role,q,score,diag)
        if ok:self._record_post_event_action(t,'ACTIVE_SUBMIT',{'side':str(side),'role':str(role),'qty':float(q),'key':f'{side}_{before}'})
        return ok

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));ok=super()._request_cancel(t,sid,reason)
        if ok:self._record_post_event_action(t,'PASSIVE_CANCEL',{'slotId':int(sid),'key':str(key),'reason':str(reason)})
        return ok

    # ---- exact R3.03 shared-parent accounting, but bound to the V8 class already in this MRO ----
    def _v8_submit_same_identity(self,t,side,role,p,q,proj,split):
        cls=next((c for c in type(self).mro() if c.__name__=='RepairOverflowSplitSim'),None)
        if cls is None:raise RuntimeError('RepairOverflowSplitSim absent from exact R2.64 MRO')
        return cls._submit_role_v8(self,t,side,role,p,q,proj,split)

    def _shared_live_keys(self):
        out=[]
        for key in list(self.r303SharedKeys):
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:out.append(key)
        return out

    def _reserved_repair_quota(self,repair_side=None):
        total=float(super()._reserved_repair_quota(repair_side))
        if not self.r303Parent:return total
        rs=repair_side or self._repair_side()
        if rs!=self.r303Parent['side'] or int(self.scopeGeneration)!=int(self.r303Parent['generation']):return total
        shared_owned=[];slot_values=set(str(x) for x in self.slot_key.values())
        for key in list(self.r303SharedKeys):
            o=self.orders.get(key)
            if o is None:continue
            if str(o['side'])!=rs or int(self.key_scope_gen.get(key,-1))!=int(self.r303Parent['generation']):continue
            owns=(str(key) in slot_values)
            if not owns:
                try:owns=str(self.snap(o).get('status') or '').upper() not in v2.TERMINAL_STATUSES
                except Exception:owns=True
            if not owns:continue
            shared_owned.append(key);total-=max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0)))
        if shared_owned:
            st=self.r303Ledger.describe_parent(int(self.r303Parent['pid']))
            if st is not None:total+=max(0.0,float(st.get('remainingDebt') or 0.0))
        return max(0.0,float(total))

    def _reserved_repair_overflow_risk(self):
        total=0.0
        for _,key,o,role in self._live_role_rows():
            if role not in REPAIR_ROLES or key in self.r303SharedKeys:continue
            oq=max(0.0,float(self.keyOverflowQtyRemaining.get(key,0.0)))
            if oq>EPS:total+=oq*float(o['price'])
        return float(total)

    def _risk_authority_current_generation(self):
        base=float(super()._risk_authority_current_generation());a=self.r303Authority
        if a and int(a.get('generation',-1))==int(self.scopeGeneration):base+=float(a.get('held',0.0))+float(a.get('spent',0.0))
        return base

    def _audit_r247(self):
        super()._audit_r247();a=self.r303Authority
        if a:
            err=abs(float(a['authorized'])-float(a['held'])-float(a['spent'])-float(a['released']))
            self.r303AuthorityOverrun=max(self.r303AuthorityOverrun,err,max(0.0,float(a['spent'])-float(a['authorized'])))

    def _composite_submit(self,t,end):
        s=self.spec;ob=self._obligation_current();gen=int(s['generation'])
        if not ob or int(self.scopeGeneration)!=gen:return False
        live=self._live_dedicated_repair_rows(gen)
        if len(live)!=1:return False
        sibling_key,sibling_o=live[0];quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live)
        outstanding=max(0.0,float(ob.get('outstanding') or 0.0))
        if quota+EPS<outstanding:return False
        thesis=str(s['thesisSide']);repair_side=self._repair_side()
        if thesis!=repair_side or int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS or self._has_stale_scope_reservation() or len(self.slot_key)+len(self.activeKeys)>=self.max_slots:return False
        if self._samegen_expand_live(gen,str(ob.get('scopeSideAtBirth') or self.scopeSide)):return False
        p=float(s['composite']['price']);debt=max(0.0,float(self._scope_debt_qty()))
        try:ss=self.snap(sibling_o);sib_rem=float(ss.get('leavesQty')) if ss.get('leavesQty') is not None else self._remaining(sibling_key)
        except Exception:sib_rem=self._remaining(sibling_key)
        venue=1.0/p;unreserved=max(0.0,debt-max(0.0,sib_rem));q=unreserved+venue;risk=venue*p
        if not (close(p,s['composite']['price']) and close(q,s['composite']['qty']) and close(unreserved,s['composite']['unreservedDebt']) and close(risk,1.0)):
            self.triggerParityErrors.append('COMPOSITE_GEOMETRY_MISMATCH');return False
        before_n=self.n;key=f'{thesis}_{before_n}';pid=100000+gen;base_sib=float(sibling_o.get('cum') or 0.0)
        self.r303Ledger.register_carrier(str(sibling_key),pid,debt);self.r303Ledger.register_carrier(str(key),pid,debt)
        self.r303Parent={'pid':pid,'generation':gen,'side':thesis,'initialDebt':debt,'siblingKey':str(sibling_key),'contingentKey':key,'venueMin':venue,'price':p,'riskCap':risk,'bornAt':int(t)}
        self.r303SharedKeys={str(sibling_key),key};self.r303BaseCum={str(sibling_key):base_sib,key:0.0}
        self.r303Authority={'authorized':risk,'held':risk,'spent':0.0,'released':0.0,'generation':gen,'bornAt':int(t)}
        repair_floor=float(self._candidate_alone_floor(thesis,p,unreserved)) if unreserved>EPS else float(self._physical_floor());full_floor=float(self._candidate_alone_floor(thesis,p,q))
        sp={'repairQty':unreserved,'overflowQty':venue,'overflowRisk':risk,'repairOnlyFloor':repair_floor,'fullFloor':full_floor,'debt':debt,'reservedRepairBefore':quota,'availableDebtBefore':unreserved,'r303SharedParentProvisional':True}
        ok=self._v8_submit_same_identity(t,thesis,'SATELLITE_REPAIR',p,q,full_floor,sp)
        if not ok:
            self.r303Parent=None;self.r303SharedKeys=set();self.r303BaseCum={};self.r303Authority=None;return False
        self.r263GenerationUsed.add(gen);self.r303['SUBMIT']+=1;self.r264['SUBMIT']+=1;self.forkBranchKey=key
        ev={'t':int(t),'event':'R303_CONTINGENT_COMPOSITE_SUBMIT','generation':gen,'parentId':pid,'siblingKey':str(sibling_key),'contingentKey':key,'thesisSide':thesis,'scopeSide':self.scopeSide,'price':p,'physicalQty':q,'parentDebt':debt,'siblingRemaining':sib_rem,'unreservedDebt':unreserved,'venueMinOverflowCap':venue,'riskCap':risk,'repairProgressAtSubmit':float(ob.get('repaidQty') or 0.0),'physicalOccupancyAfter':len(self.slot_key)+len(self.activeKeys)}
        self.r303Events.append(ev);self.slot_history.append(ev);self._audit_r247();return True

    def _prepare_shared_fill_allocation(self,t):
        if not self.r303Parent:return
        pid=int(self.r303Parent['pid']);expected={};keys=[k for k in self.r303SharedKeys if k in self.orders];keys.sort(key=lambda k:int(self.orders[k].get('n') or 0))
        for key in keys:
            o=self.orders[key]
            try:s=self.snap(o);cur_abs=float(s.get('cumExecQty') or 0.0)
            except Exception:continue
            rel=max(0.0,cur_abs-float(self.r303BaseCum.get(key,0.0)))
            ar=self.r303Ledger.allocate_cumulative(str(key),pid,rel,float(self.r303Parent['initialDebt']))
            if ar is None:continue
            expected[key]={'repair':float(ar.repair_increment),'overflow':float(ar.overflow_increment),'fill':float(ar.fill_increment),'debtBefore':float(ar.debt_before),'debtAfter':float(ar.debt_after)}
            self.keyRepairQuotaRemaining[key]=float(ar.repair_increment);self.keyOverflowQtyRemaining[key]=float(ar.overflow_increment)
        if expected:self.r303ExpectedByReceipt[int(t)]=expected

    def _reconcile_r303_after_process(self,t,start):
        expected=self.r303ExpectedByReceipt.get(int(t),{});actual={}
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'))
            if key not in self.r303SharedKeys:continue
            actual[key]={'repair':float(ev.get('repairAllocated') or 0.0),'overflow':float(ev.get('overflowRealized') or 0.0),'fill':float(ev.get('fillInc') or 0.0),'price':float(ev.get('price') or 0.0),'side':str(ev.get('side'))}
        overflow_birth=0.0
        for key in set(expected)|set(actual):
            e=expected.get(key,{'repair':0.0,'overflow':0.0,'fill':0.0});a=actual.get(key,{'repair':0.0,'overflow':0.0,'fill':0.0,'price':0.0})
            ok=all(abs(float(e[x])-float(a[x]))<=1e-8 for x in ('repair','overflow','fill'))
            if not ok:self.r303SplitMismatch+=1
            self.r303RepairQty+=float(a['repair']);self.r303OverflowQty+=float(a['overflow']);overflow_birth+=float(a['overflow'])
            orisk=float(a['overflow'])*float(a.get('price') or 0.0);self.r303OverflowRisk+=orisk
            if orisk>EPS and self.r303Authority:
                take=min(float(self.r303Authority['held']),orisk);self.r303Authority['held']-=take;self.r303Authority['spent']+=orisk
                self.r303AuthorityOverrun=max(self.r303AuthorityOverrun,max(0.0,float(self.r303Authority['spent'])-float(self.r303Authority['authorized'])))
            self.r303Events.append({'t':int(t),'event':'R303_SHARED_FILL_ALLOCATED','key':key,'expected':e,'actual':a,'match':ok})
        if overflow_birth>EPS and self.scopeSide in {'UP','DOWN'}:
            ngen=int(self.scopeGeneration)
            if self.r303Authority:self.r303Authority['generation']=ngen
            ob=self.riskRepairObligations.setdefault(ngen,{'generation':ngen,'scopeSideAtBirth':self.scopeSide,'bornAt':int(t),'bornFromRiskKey':'R303_SHARED_OVERFLOW','bornQty':0.0,'outstanding':0.0,'repaidQty':0.0,'passiveRepaidQty':0.0,'activeRepaidQty':0.0,'carrierSubmits':0,'carrierFills':0,'zeroFillTerminals':0,'closedAt':None,'closeReason':None})
            ob['bornQty']+=overflow_birth;ob['outstanding']+=overflow_birth;self.r303NewObligationQty+=overflow_birth;self.riskTrancheGenerationUsed.add(ngen);self.r303['OVERFLOW_OBLIGATION_BORN']+=1
        if self.r303Authority and float(self.r303Authority['held'])>EPS and not self._shared_live_keys():
            q=float(self.r303Authority['held']);self.r303Authority['held']=0.0;self.r303Authority['released']+=q
        self._audit_r247()

    def _trigger_snapshot(self,t):
        s=self.spec;gen=int(self.scopeGeneration);ob=self._obligation_current();live=self._live_dedicated_repair_rows(gen)
        q=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live)
        snap={'t':int(t),'generation':gen,'scopeSide':self.scopeSide,'repairSide':self._repair_side(),'thesisSide':self.intentThesisSide,
              'obligationOutstanding':float(ob.get('outstanding') if ob else 0.0),'obligationRepaid':float(ob.get('repaidQty') if ob else 0.0),
              'liveRepairKeys':[str(k) for k,_ in live],'representedRepairQuota':q,'slots':len(self.slot_key),'active':len(self.activeKeys),'payoff':self._payoff()}
        errs=[]
        if gen!=int(s['generation']):errs.append('GENERATION')
        if str(self.scopeSide)!=str(s['scopeSide']):errs.append('SCOPE_SIDE')
        if str(self._repair_side())!=str(s['repairSide']):errs.append('REPAIR_SIDE')
        if not close(snap['obligationOutstanding'],s['obligationOutstanding']):errs.append('OBLIGATION')
        if [str(k) for k,_ in live]!=[str(s['siblingKey'])]:errs.append('SIBLING_KEY')
        if not close(q,s['siblingRemaining']):errs.append('REPRESENTED_QUOTA')
        self.triggerParityErrors.extend(errs);return snap

    def _arm_fork(self,t,branch_key=None,pre_state=None):
        self.forkTriggered=True;self.forkActive=True;self.forkBranchKey=branch_key;self.forkSiblingKey=str(self.spec['siblingKey'])
        involved=[self.forkSiblingKey]+([branch_key] if branch_key else [])
        self.forkInitialCum={k:self._live_status(k)['cum'] for k in involved};self.forkExistingActiveCum={k:self._live_status(k)['cum'] for k in list(self.activeKeys)};self.forkActiveFillQtyAtTrigger=float(getattr(self,'activeFillQty',0.0))
        ob=self._obligation_current();self.forkTrigger={'t':int(t),'action':self.action,'state':(pre_state if pre_state is not None else self._trigger_snapshot(t)),'branchKey':branch_key,
          'obligationOutstanding':float(ob.get('outstanding') if ob else 0.0),'obligationRepaid':float(ob.get('repaidQty') if ob else 0.0),
          'repairProgressClocks':int(self.scopeRepairProgressClocks),'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,'payoff':self._payoff()}

    def _try_r263(self,t,end):
        target=int(self.spec['t'])
        if int(t)!=target or self.forkTriggered:return super()._try_r263(t,end)
        pre=self._trigger_snapshot(t)
        if self.triggerParityErrors: return False
        if self.action=='KEEP_REPAIR':
            self._arm_fork(t,None,pre);return False
        if self.action=='ORDINARY_REEXPAND':
            before_n=self.n;ok=super()._try_r263(t,end);key=f"{self.spec['ordinary']['side']}_{before_n}" if ok else None
            if not ok:self.triggerParityErrors.append('ORDINARY_NOT_SUBMITTED');return False
            o=self.orders.get(key)
            if not o or not(close(o['price'],self.spec['ordinary']['price']) and close(o['qty'],self.spec['ordinary']['qty'])):self.triggerParityErrors.append('ORDINARY_GEOMETRY')
            self._arm_fork(t,key,pre);return ok
        if self.action=='R303_CONTINGENT_COMPOSITE':
            ok=self._composite_submit(t,end)
            if not ok:self.triggerParityErrors.append('R303_NOT_SUBMITTED');return False
            key=self.forkBranchKey;self._arm_fork(t,key,pre);return True
        raise AssertionError(self.action)

    # Freeze new Manager decisions only until first structural event; execution mechanics continue.
    def _risk_contract_if_needed(self,t):
        if self.forkActive:self.forkManagerBlocks['RISK_CONTRACT']+=1;return
        return super()._risk_contract_if_needed(t)
    def _reanchor_stale(self,t):
        if self.forkActive:self.forkManagerBlocks['REANCHOR']+=1;return
        return super()._reanchor_stale(t)
    def _open_one_option(self,t,qv,end):
        if self.forkActive:self.forkManagerBlocks['OPEN_OPTION']+=1;return
        return super()._open_one_option(t,qv,end)

    def _maybe_resolve_fork(self,t):
        if not self.forkActive or self.forkResolved:return
        reasons=[]
        for key,basecum in self.forkInitialCum.items():
            st=self._live_status(key)
            if st['cum']>basecum+EPS:reasons.append({'type':'CONFIRMED_INVOLVED_FILL','key':key,'fillDelta':st['cum']-basecum})
            if st['status'] in v2.TERMINAL_STATUSES:reasons.append({'type':'INVOLVED_QUEUE_TERMINAL','key':key,'status':st['status']})
        ob=self.riskRepairObligations.get(int(self.spec['generation']))
        if ob:
            if float(ob.get('repaidQty') or 0.0)>float(self.forkTrigger['obligationRepaid'])+EPS:reasons.append({'type':'RESPONSIBILITY_PAYMENT','paidDelta':float(ob.get('repaidQty') or 0.0)-float(self.forkTrigger['obligationRepaid'])})
            if ob.get('closeReason') not in (None,''):reasons.append({'type':'RESPONSIBILITY_TRANSITION','closeReason':ob.get('closeReason')})
        if int(self.scopeGeneration)!=int(self.forkTrigger['scopeGeneration']) or self.scopeSide!=self.forkTrigger['scopeSide']:
            reasons.append({'type':'SCOPE_TRANSITION','generation':int(self.scopeGeneration),'scopeSide':self.scopeSide})
        if int(self.scopeRepairProgressClocks)>int(self.forkTrigger['repairProgressClocks']):reasons.append({'type':'NEXT_AUTHORITY_MATERIALIZATION','repairProgressClock':int(self.scopeRepairProgressClocks)})
        for key,c0 in self.forkExistingActiveCum.items():
            st=self._live_status(key)
            if st['cum']>c0+EPS:reasons.append({'type':'INHERITED_ACTIVE_FILL','key':key,'fillDelta':st['cum']-c0})
        if reasons:
            self.forkResolved=True;self.forkActive=False;self.forkEvent={'t':int(t),'reasons':reasons,'payoff':self._payoff(),
              'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,'obligation':({k:ob.get(k) for k in ['outstanding','repaidQty','bornQty','passiveRepaidQty','activeRepaidQty','closeReason']} if ob else None),
              'r303OverflowQty':float(self.r303OverflowQty),'r303OverflowRisk':float(self.r303OverflowRisk),'activeFillQtyDelta':float(getattr(self,'activeFillQty',0.0))-float(self.forkActiveFillQtyAtTrigger)}

    def process(self,t):
        if self.r303Parent:self._prepare_shared_fill_allocation(int(t))
        start=len(self.splitEvents);super().process(t)
        if self.r303Parent:self._reconcile_r303_after_process(int(t),start)
        self._maybe_resolve_fork(int(t))

    def run_exact(self,winner):
        r=super().run_r264(winner);self._audit_r247()
        parent=self.r303Ledger.describe_parent(int(self.r303Parent['pid'])) if self.r303Parent else None
        cons=True
        if parent is not None:cons=abs(float(parent['repairPaid'])+float(parent['remainingDebt'])-float(parent['initialDebt']))<=1e-8
        correct=bool(r.get('r264CorrectnessPass')) and not self.triggerParityErrors and self.r303SplitMismatch==0 and self.r303AuthorityOverrun<=EPS and self.r303OverflowRisk<=1.0+1e-8 and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS and cons
        return {'marketId':self.marketId,'action':self.action,'triggered':self.forkTriggered,'resolved':self.forkResolved,'triggerParityErrors':self.triggerParityErrors,
          'trigger':self.forkTrigger,'firstStructuralEvent':self.forkEvent,'postEventFirstManagerAction':self.postEventFirstManagerAction,'managerBlocksDuringFork':dict(self.forkManagerBlocks),'branchKey':self.forkBranchKey,
          'r303':{'stats':dict(self.r303),'events':self.r303Events[:500],'parent':self.r303Parent,'parentState':parent,'splitMismatch':self.r303SplitMismatch,
                  'repairQty':self.r303RepairQty,'overflowQty':self.r303OverflowQty,'overflowRisk':self.r303OverflowRisk,'newObligationQty':self.r303NewObligationQty,'authority':self.r303Authority,'authorityOverrun':self.r303AuthorityOverrun},
          'terminal':{'pnlPostHocOnly':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),'gap':float(r['best'])-float(r['floor']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'activeRepaidQty':float(r.get('riskRepairActiveRepaidQty',0.0))},
          'correct':bool(correct)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946317,1946640');ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_ma_v1b_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            for action in ['KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE']:
                sim=MultiActionExactForkSim(tmp/f'{m}.json.xz',m,action,1,4)
                try:r=sim.run_exact(co[m]['winner'])
                finally:sim.close()
                rows.append(r);print(json.dumps({'marketId':m,'action':action,'triggered':r['triggered'],'resolved':r['resolved'],'event':(r['firstStructuralEvent'] or {}).get('reasons'),'localPayoff':(r['firstStructuralEvent'] or {}).get('payoff'),'terminal':r['terminal'],'correct':r['correct'],'errors':r['triggerParityErrors']},ensure_ascii=False),flush=True)
        # Same trigger state parity uses only pre-branch strict-past fields/payoff.
        parity={};vectors=[]
        for m in mids:
            rr=[x for x in rows if x['marketId']==m];base=next(x for x in rr if x['action']=='KEEP_REPAIR')
            bstate=(base.get('trigger') or {}).get('state')
            parity[str(m)]=all((x.get('trigger') or {}).get('state')==bstate for x in rr)
            bp=(base.get('firstStructuralEvent') or {}).get('payoff')
            for x in rr:
                ep=(x.get('firstStructuralEvent') or {}).get('payoff');ev=x.get('firstStructuralEvent')
                vec={'marketId':m,'action':x['action'],'eventT':(ev or {}).get('t'),'eventReasons':(ev or {}).get('reasons'),'resolved':x['resolved'],'correct':x['correct']}
                if ep is not None and bp is not None:
                    vec.update({'deltaBestVsKeepEvent':float(ep['best'])-float(bp['best']),'deltaFloorVsKeepEvent':float(ep['floor'])-float(bp['floor']),'deltaGapVsKeepEvent':float(ep['gap'])-float(bp['gap'])})
                vec.update({'responsibilityPaymentProgress':((ev or {}).get('obligation') or {}).get('repaidQty'),'confirmedNewRiskOverflowQty':float((ev or {}).get('r303OverflowQty') or 0.0),'confirmedNewRiskOverflowRisk':float((ev or {}).get('r303OverflowRisk') or 0.0),
                            'activeUsageCost':float((ev or {}).get('activeFillQtyDelta') or 0.0),'queueRetainedLost':(ev or {}).get('reasons')})
                vectors.append(vec)
        out={'version':'LANE_G_MULTI_ACTION_EXACT_FORK_V1B_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'actionVectors':vectors,
             'gates':{'allTriggered':all(x['triggered'] for x in rows),'allResolvedByStructuralEvent':all(x['resolved'] for x in rows),'triggerStateParity':all(parity.values()),'correctnessPass':all(x['correct'] for x in rows)},
             'triggerParityByMarket':parity,'boundary':['preregistered V1B states only','exact R2.64 behavior before trigger','branch differs only in one R2.63 authority use/representation','new Manager actions frozen only until first structural event','no fixed-time fork horizon','terminal PnL secondary post-hoc only','same realistic HFT tape/latency/queue','max4 and <=180s unchanged','no Target/winner future branch input','consumed only','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'vectors':vectors},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
