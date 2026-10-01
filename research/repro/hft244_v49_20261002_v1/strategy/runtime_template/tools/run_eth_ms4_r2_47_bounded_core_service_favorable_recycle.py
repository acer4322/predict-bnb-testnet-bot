from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_46_r240_core_active_credit_mechanism_ablation as r246

r240=r246.r240
r28=r246.r28
v2=r246.v2
r1=r246.r1
EPS=1e-9
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class BoundedCoreServiceFavorableRecycleSim(r246.R240OneCoreActiveMechanismSim):
    """R2.47 research-only integration.

    Base is exact R2.40.  The added Core-liveness Active must reserve existing
    continuation authority before submit.  Only credit minted by THAT added Core
    service is quarantined from generic continuation.  Ordinary Satellite Active
    credit remains frozen R2.40 behavior.

    Confirmed Repair allocations also create one-use scheduling lots.  Ordinary
    Expand receives first claim.  Only when ordinary monetary credit is
    insufficient may an otherwise-idle receipt use fully-covered lots with
    repairPrice + expandPrice <= 1 to authorize one venue-min re-expand.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        if fanout_limit!=1 or max_slots!=4: raise ValueError('Frozen CAP1 capacity required')
        self.serviceLedger={}; self.serviceEvents=[]; self.r247=Counter(); self.r247Events=[]
        self.serviceChecks={
            'serviceConservationErrorMax':0.0,
            'serviceOverfillNotionalMax':0.0,
            'combinedAuthorityExcessMax':0.0,
            'coreServiceCreditQuarantineShortfall':0.0,
            'duplicateServiceOwnershipCount':0,
            'staleCoreServiceSubmitCount':0,
            'nonzeroNativeSubmitRcCount':0,
            'replenishmentReservedLotOverfillQty':0.0,
        }
        self._coreServiceContext=False
        self.ordinaryActiveMintedCredit=0.0
        self.ordinaryActiveCreditGeneration=0
        self.repairLots=[]; self.nextLotId=1
        self.replenishmentKeys=set(); self.hybridKeys=set(); self.hybridMeta={}; self.keyLotReservations={}
        # We bypass R246's own process quarantine and implement Core-only quarantine below.
        super().__init__(tape,'ACTIVE',fanout_limit,max_slots)

    # ---------- finite service authority for the added Core-liveness actuator ----------
    def _service_claim(self):
        if self.scopeSide is None:return 0.0
        return float(sum(float(x['held'])+float(x['spent']) for x in self.serviceLedger.values()
                         if int(x['generation'])==int(self.scopeGeneration) and x['scopeSide']==self.scopeSide))

    def _reserved_current_expand_risk(self):
        # Explicit monetary reservation accounting: replenishment SATELLITE_EXPAND
        # is authorized by one-use Repair lots and must not also reserve money credit.
        if self.scopeSide is None:return 0.0
        total=0.0
        for _,key,o,role in self._live_role_rows():
            if int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            p=float(o['price'])
            if role=='SATELLITE_EXPAND':
                if key in self.hybridKeys:
                    mq=max(0.0,float(self.hybridMeta.get(key,{}).get('monetaryQtyRemaining',0.0)))
                    if mq>EPS:total+=mq*p
                elif key not in self.replenishmentKeys:
                    rem=float(self._remaining(key))
                    if rem>EPS:total+=rem*p
            if role in REPAIR_ROLES:
                oq=max(0.0,float(self.keyOverflowQtyRemaining.get(key,0.0)))
                if oq>EPS:total+=oq*p
        return float(total)

    def _available_expand_risk_credit(self):
        if self.scopeSide is None or self._has_stale_scope_reservation():return 0.0
        if any(float(x['held'])>EPS and int(x['generation'])!=int(self.scopeGeneration)
               for x in self.serviceLedger.values()):return 0.0
        return max(0.0,float(self.scopeRiskCreditTotal)-float(self.scopeRiskCreditConsumed)
                   -float(self._reserved_current_expand_risk())-float(self._service_claim()))

    def _available_core_service_authority(self):
        # Provenance fence: ordinary Active-minted credit remains spendable by the
        # frozen R2.40 continuation path, but cannot recursively sponsor the added
        # Core rescue service.  This is a resource-origin rule, not a market gate.
        active_origin=float(self.ordinaryActiveMintedCredit) if int(self.ordinaryActiveCreditGeneration)==int(self.scopeGeneration) else 0.0
        return max(0.0,float(self._available_expand_risk_credit())-active_origin)

    def _svc_event(self,t,event,**kw):
        e={'t':int(t),'event':event,**kw}; self.serviceEvents.append(e); self.slot_history.append(e)

    def _try_core_active(self,t:int):
        self._coreServiceContext=True
        try:return super()._try_core_active(t)
        finally:self._coreServiceContext=False

    def _submit_active(self,t,side,role,q,score,diag):
        if not self._coreServiceContext:
            # Frozen ordinary Satellite Active behavior, including its native credit lifecycle.
            return super()._submit_active(t,side,role,q,score,diag)
        ev=self.pendingCoreEvidence
        if (not ev or self.scopeSide is None or int(ev['generation'])!=int(self.scopeGeneration)
                or side!=self._repair_side()):
            self.serviceChecks['staleCoreServiceSubmitCount']+=1; return False
        qv=r1.v2.base.quotes(self.book)
        if not qv or qv.get(side,{}).get('ask') is None:return False
        ap=float(qv[side]['ask']); charge=ap*float(q); available=float(self._available_core_service_authority())
        if not math.isfinite(charge) or charge<=0:return False
        if charge>available+EPS:
            self.r247['CORE_SERVICE_WAIT_AUTHORITY']+=1
            return False
        # New Core service shares physical carrier capacity; inherited Satellite Active semantics are untouched.
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:
            self.r247['CORE_SERVICE_WAIT_CARRIER_CAP']+=1; return False
        predicted=f'{side}_{self.n}'
        if predicted in self.serviceLedger:
            self.serviceChecks['duplicateServiceOwnershipCount']+=1; raise RuntimeError('duplicate service owner')
        start=len(self.executionDecisions)
        ok=super()._submit_active(t,side,role,q,score,diag)
        if not ok:return False
        ss=[x for x in self.executionDecisions[start:] if x.get('event')=='MS4_R2_ACTIVE_REPAIR_SUBMIT']
        if len(ss)!=1 or ss[0].get('key')!=predicted:raise RuntimeError('core service submit owner mismatch')
        if int(ss[0].get('submitRc') or 0)!=0:self.serviceChecks['nonzeroNativeSubmitRcCount']+=1
        actual_charge=float(ss[0]['activePrice'])*float(ss[0]['qty'])
        self.serviceLedger[predicted]={'key':predicted,'sourceKey':ev['sourceKey'],'generation':int(self.scopeGeneration),
            'scopeSide':self.scopeSide,'authorized':actual_charge,'held':actual_charge,'spent':0.0,'released':0.0,
            'repairAllocated':0.0,'terminal':None}
        self.r247['CORE_SERVICE_SUBMIT']+=1
        self._svc_event(t,'R247_CORE_SERVICE_AUTHORITY_RESERVED',**self.serviceLedger[predicted],availableBefore=available)
        self._audit_r247(); return True

    def _audit_r247(self):
        for x in self.serviceLedger.values():
            err=abs(float(x['authorized'])-float(x['held'])-float(x['spent'])-float(x['released']))
            self.serviceChecks['serviceConservationErrorMax']=max(self.serviceChecks['serviceConservationErrorMax'],err)
        if self.scopeSide is not None:
            committed=float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk())+self._service_claim()
            self.serviceChecks['combinedAuthorityExcessMax']=max(self.serviceChecks['combinedAuthorityExcessMax'],
                max(0.0,committed-float(self.scopeRiskCreditTotal)))

    # ---------- one-use favorable Repair-lot authority ----------
    def _clean_lots(self):
        g=int(self.scopeGeneration)
        self.repairLots=[x for x in self.repairLots if int(x['generation'])==g and float(x['remaining'])>EPS]

    def _pending_ordinary_claims(self):
        self._clean_lots(); free={int(x['lotId']):float(x['remaining']) for x in self.repairLots}; claims=Counter()
        for _,key,o,role in self._live_role_rows(role='SATELLITE_EXPAND'):
            if key in self.replenishmentKeys or int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            need=float(self._remaining(key)); side=str(o['side']); pe=float(o['price'])
            for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
                if need<=EPS:break
                lid=int(x['lotId']); avail=max(0.0,float(free.get(lid,0.0)))
                if avail<=EPS or x['side']!=side or float(x['repairPrice'])+pe>1.0+EPS:continue
                take=min(need,avail); free[lid]=avail-take; claims[lid]+=take; need-=take
        return claims

    def _gross_eligible_qty(self,side,pe):
        self._clean_lots()
        return float(sum(float(x['remaining']) for x in self.repairLots
                         if x['side']==side and float(x['repairPrice'])+float(pe)<=1.0+EPS))

    def _eligible_qty(self,side,pe):
        claims=self._pending_ordinary_claims()
        return float(sum(max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0))) for x in self.repairLots
                         if x['side']==side and float(x['repairPrice'])+float(pe)<=1.0+EPS))

    def _reserve_lots(self,side,pe,q):
        claims=self._pending_ordinary_claims(); need=float(q); out=[]
        elig=[x for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t'])))
              if x['side']==side and float(x['repairPrice'])+float(pe)<=1.0+EPS and float(x['remaining'])>EPS]
        if sum(max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0))) for x in elig)+EPS<need:return None
        for x in elig:
            if need<=EPS:break
            free=max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0)))
            if free<=EPS:continue
            take=min(need,free); x['remaining']-=take; need-=take
            out.append({'lot':x,'remaining':take,'authorized':take,'pairSum':float(x['repairPrice'])+float(pe)})
        return out if need<=EPS else None

    def _rollback_lots(self,res):
        for a in res or []:
            q=float(a.get('remaining') or 0.0)
            if q>EPS:a['lot']['remaining']+=q; a['remaining']=0.0

    def _consume_reserved_lots(self,key,q):
        rem=float(q); used=[]
        for a in self.keyLotReservations.get(key,[]):
            if rem<=EPS:break
            avail=float(a.get('remaining') or 0.0)
            if avail<=EPS:continue
            take=min(rem,avail); a['remaining']=avail-take; rem-=take
            used.append({'lotId':int(a['lot']['lotId']),'repairPrice':float(a['lot']['repairPrice']),
                         'qty':take,'pairSum':float(a['pairSum'])})
        return used,rem

    def _consume_lots_for_ordinary_expand(self,side,pe,q):
        self._clean_lots(); rem=float(q); used=[]
        for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
            if rem<=EPS:break
            if x['side']!=side or float(x['repairPrice'])+float(pe)>1.0+EPS or float(x['remaining'])<=EPS:continue
            take=min(rem,float(x['remaining'])); x['remaining']-=take; rem-=take
            used.append({'lotId':int(x['lotId']),'repairPrice':float(x['repairPrice']),'qty':take,
                         'pairSum':float(x['repairPrice'])+float(pe)})
        if used:self.r247['ORDINARY_EXPAND_CONSUMED_FAVORABLE_LOT']+=1
        return used,rem

    def _release_terminal_replenishment(self,t):
        for key,res in list(self.keyLotReservations.items()):
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:continue
            released=0.0
            for a in res:
                q=float(a.get('remaining') or 0.0)
                if q>EPS:a['lot']['remaining']+=q; a['remaining']=0.0; released+=q
            if released>EPS:self.r247['REPLENISHMENT_UNUSED_LOT_RELEASE']+=1
            self.keyLotReservations.pop(key,None)

    def _has_live_replenishment(self):
        for key in (set(self.replenishmentKeys)|set(self.hybridKeys)):
            if int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:return True
        return False

    def _try_favorable_replenishment(self,t,end):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS or self.scopeSide is None:return False
        if self._has_stale_scope_reservation() or self._has_live_replenishment():return False
        side=str(self.scopeSide)
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:return False
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return False
        p,q,proj,split=cand
        risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)))
        credit=float(self._available_expand_risk_credit())
        if credit+EPS>=risk:return False # ordinary R2.40/R2.47 monetary continuation owns it
        gross=self._gross_eligible_qty(side,p); elig=self._eligible_qty(side,p)
        if elig+EPS<q:
            if gross+EPS>=q:self.r247['REPLENISHMENT_PENDING_ORDINARY_CLAIM_BLOCK']+=1
            elif elig>EPS:self.r247['REPLENISHMENT_LOT_BELOW_VENUE_MIN']+=1
            return False
        res=self._reserve_lots(side,p,q)
        if not res:return False
        before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',p,q,proj,None):
            self._rollback_lots(res); return False
        key=f'{side}_{before_n}'; self.replenishmentKeys.add(key); self.keyLotReservations[key]=res
        self.r247['FAVORABLE_REPLENISHMENT_SUBMIT']+=1
        ev={'t':int(t),'event':'R247_FAVORABLE_REPLENISHMENT_SUBMIT','key':key,'generation':int(self.scopeGeneration),
            'side':side,'expandPrice':float(p),'qty':float(q),'ordinaryAvailableCredit':credit,'riskCost':risk,
            'reservedLots':[{'lotId':int(a['lot']['lotId']),'repairPrice':float(a['lot']['repairPrice']),
                             'qty':float(a['authorized']),'pairSum':float(a['pairSum'])} for a in res]}
        self.r247Events.append(ev); self.slot_history.append(ev); return True

    def _open_one_option(self,t,qv,end):
        super()._open_one_option(t,qv,end)
        if self._last_new_receipt==int(t):return
        self._try_favorable_replenishment(t,end)

    def _refresh_slots(self,t:int):
        super()._refresh_slots(t); self._release_terminal_replenishment(t)

    def process(self,t):
        old_scope=self.scopeSide; old_gen=int(self.scopeGeneration); start=len(self.splitEvents)
        # Exact R2.40 physical/accounting path; skip R246 generic Core-credit process.
        r240.HandoffRepairCreditQuarantineSim.process(self,t)
        same=(self.scopeSide==old_scope and int(self.scopeGeneration)==old_gen)
        if int(self.scopeGeneration)!=old_gen or self.scopeSide!=old_scope:
            self.ordinaryActiveMintedCredit=0.0
            self.ordinaryActiveCreditGeneration=int(self.scopeGeneration)
        elif int(self.ordinaryActiveCreditGeneration)!=int(self.scopeGeneration):
            self.ordinaryActiveMintedCredit=0.0
            self.ordinaryActiveCreditGeneration=int(self.scopeGeneration)
        new=self.splitEvents[start:]
        core_credit=0.0
        for ev in new:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key')); role=str(ev.get('role')); side=str(ev.get('side'))
            gen=int(ev.get('generationAtSubmit') or -1); price=float(ev.get('price') or 0.0)
            inc=float(ev.get('fillInc') or 0.0); rq=float(ev.get('repairAllocated') or 0.0)
            if (same and rq>EPS and key in self.activeMeta and key!=str(self.coreActiveKey)
                    and gen==old_gen and old_scope is not None and side!=old_scope):
                minted=rq*(1.0-price)
                self.ordinaryActiveMintedCredit+=minted
                self.r247['ORDINARY_ACTIVE_CREDIT_PROVENANCE_MINT']+=1
                self.r247Events.append({'t':int(t),'event':'R247_ORDINARY_ACTIVE_CREDIT_PROVENANCE_MINT',
                    'key':key,'credit':minted,'pool':self.ordinaryActiveMintedCredit,'generation':old_gen})
            # Service notional moves held -> spent on confirmed fill.
            sx=self.serviceLedger.get(key)
            if sx is not None and inc>EPS:
                debit=inc*price
                self.serviceChecks['serviceOverfillNotionalMax']=max(self.serviceChecks['serviceOverfillNotionalMax'],max(0.0,debit-float(sx['held'])))
                sx['held']=max(0.0,float(sx['held'])-debit); sx['spent']+=debit; sx['repairAllocated']+=rq
                self._svc_event(t,'R247_CORE_SERVICE_AUTHORITY_SPENT',key=key,fillNotional=debit,held=sx['held'],spent=sx['spent'])
            # Every actual Repair allocation can become a one-use favorable pairing lot.
            if role in REPAIR_ROLES and rq>EPS and gen==int(self.scopeGeneration):
                surplus='DOWN' if side=='UP' else 'UP'
                lot={'lotId':self.nextLotId,'t':int(t),'generation':gen,'side':surplus,'repairPrice':price,
                     'remaining':rq,'bornQty':rq,'sourceKey':key,'sourceRole':role}
                self.nextLotId+=1; self.repairLots.append(lot); self.r247['REPAIR_LOT_BORN']+=1
            # Expand consumes favorable lots so the same Repair quantity cannot later authorize again.
            if role=='SATELLITE_EXPAND' and inc>EPS:
                if key in self.hybridKeys:
                    hm=self.hybridMeta[key]; pair_fill=min(inc,max(0.0,float(hm.get('pairQtyRemaining',0.0))))
                    used,left_pair=self._consume_reserved_lots(key,pair_fill)
                    hm['pairQtyRemaining']=max(0.0,float(hm.get('pairQtyRemaining',0.0))-pair_fill)
                    monetary_fill=max(0.0,inc-pair_fill)
                    hm['monetaryQtyRemaining']=max(0.0,float(hm.get('monetaryQtyRemaining',0.0))-monetary_fill)
                    # Frozen V8 consumed full Expand notional. Reverse only the favorable-lot-covered portion;
                    # the unmatched portion remains genuine monetary-credit spend.
                    if gen==int(self.scopeGeneration) and pair_fill>EPS:
                        self.scopeRiskCreditConsumed=max(0.0,float(self.scopeRiskCreditConsumed)-pair_fill*price)
                    gain=sum((1.0-float(x['pairSum']))*float(x['qty']) for x in used)
                    self.r247['HYBRID_FAVORABLE_FILL']+=1; self.r247['HYBRID_FAVORABLE_PAIR_QTY_MILLI']+=int(round(pair_fill*1000))
                    self.r247['HYBRID_FAVORABLE_MONETARY_QTY_MILLI']+=int(round(monetary_fill*1000))
                    self.r247['HYBRID_FAVORABLE_MATCHED_GAIN_MICRO']+=int(round(gain*1_000_000))
                    if left_pair>EPS:self.serviceChecks['replenishmentReservedLotOverfillQty']=max(self.serviceChecks['replenishmentReservedLotOverfillQty'],left_pair)
                    e={'t':int(t),'event':'R247_HYBRID_FAVORABLE_FILL','key':key,'fillQty':inc,'expandPrice':price,
                       'pairCoveredQty':pair_fill,'monetaryCoveredQty':monetary_fill,'usedLots':used,'unmatchedPairQty':left_pair,
                       'matchedMarginalFloorGain':gain,'pairQtyRemaining':hm['pairQtyRemaining'],'monetaryQtyRemaining':hm['monetaryQtyRemaining']}
                    self.r247Events.append(e);self.slot_history.append(e)
                elif key in self.replenishmentKeys:
                    used,left=self._consume_reserved_lots(key,inc)
                    gain=sum((1.0-float(x['pairSum']))*float(x['qty']) for x in used)
                    if gen==int(self.scopeGeneration):self.scopeRiskCreditConsumed=max(0.0,float(self.scopeRiskCreditConsumed)-inc*price)
                    self.r247['FAVORABLE_REPLENISHMENT_FILL']+=1
                    self.r247['FAVORABLE_REPLENISHMENT_FILL_QTY_MILLI']+=int(round(inc*1000))
                    self.r247['FAVORABLE_REPLENISHMENT_MATCHED_GAIN_MICRO']+=int(round(gain*1_000_000))
                    if left>EPS:self.serviceChecks['replenishmentReservedLotOverfillQty']=max(self.serviceChecks['replenishmentReservedLotOverfillQty'],left)
                    e={'t':int(t),'event':'R247_FAVORABLE_REPLENISHMENT_FILL','key':key,'fillQty':inc,'expandPrice':price,
                       'usedLots':used,'unmatchedAuthorizedQty':left,'matchedMarginalFloorGain':gain}
                    self.r247Events.append(e); self.slot_history.append(e)
                else:self._consume_lots_for_ordinary_expand(side,price,inc)
            # Core service Repair credit is quarantined from GENERIC continuation only.
            if self.coreActiveKey is not None and key==str(self.coreActiveKey) and rq>EPS:
                self.coreActiveRepairAllocated+=rq; self.coreActiveFillEvents+=1
                cc=rq*(1.0-price); self.coreActiveCreditObserved+=cc
                if same and old_scope is not None and gen==old_gen and side!=old_scope:core_credit+=cc
        # Native terminal reconciliation must happen before releasing unfilled service authority.
        for key,sx in self.serviceLedger.items():
            if sx['terminal'] is not None or key in self.activeKeys:continue
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:continue
            sx['released']+=sx['held']; sx['held']=0.0; sx['terminal']=st
            self._svc_event(t,'R247_CORE_SERVICE_AUTHORITY_TERMINAL',key=key,status=st,released=sx['released'])
        if core_credit>EPS:
            before=float(self.scopeRiskCreditTotal)
            protected=float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk())+self._service_claim()
            removed=min(core_credit,max(0.0,before-protected)); self.scopeRiskCreditTotal=before-removed
            short=max(0.0,core_credit-removed)
            self.serviceChecks['coreServiceCreditQuarantineShortfall']+=short
            self.r247['CORE_SERVICE_CREDIT_QUARANTINE']+=1
            self.r247Events.append({'t':int(t),'event':'R247_CORE_SERVICE_CREDIT_QUARANTINED','eligible':core_credit,
                                    'removed':removed,'shortfall':short,'before':before,'after':self.scopeRiskCreditTotal,
                                    'protected':protected})
        self._clean_lots(); self._audit_r247()

    def run_r247(self,winner):
        # Use R2.40 runner; dynamic dispatch executes all R2.47 overrides.
        r=r240.HandoffRepairCreditQuarantineSim.run_r240(self,winner)
        self._audit_r247()
        if self.coreActiveMaterialized and self.coreActiveKey not in self.serviceLedger:
            self.serviceChecks['duplicateServiceOwnershipCount']+=1
        r.update({
            'r247Version':'MS4_R2_47_BOUNDED_CORE_SERVICE_FAVORABLE_RECYCLE',
            'r247Stats':dict(self.r247),'r247Events':self.r247Events[:3000],
            'r247ServiceChecks':dict(self.serviceChecks),'r247ServiceCorrectnessPass':all(float(v)<=EPS for v in self.serviceChecks.values()),
            'r247ServiceLedger':list(self.serviceLedger.values()),'r247ServiceEvents':self.serviceEvents[:2000],
            'r247CoreActiveMaterialized':bool(self.coreActiveMaterialized),'r247CoreActiveKey':self.coreActiveKey,
            'r247CoreIntervention':self.coreActiveIntervention,
            'r247CoreActiveFillEvents':int(self.coreActiveFillEvents),'r247CoreActiveRepairAllocated':float(self.coreActiveRepairAllocated),
            'r247CoreActiveCreditObserved':float(self.coreActiveCreditObserved),
            'r247FavorableReplenishmentSubmits':int(self.r247.get('FAVORABLE_REPLENISHMENT_SUBMIT',0)),
            'r247FavorableReplenishmentFills':int(self.r247.get('FAVORABLE_REPLENISHMENT_FILL',0)),
            'r247FavorableMatchedFloorGain':float(self.r247.get('FAVORABLE_REPLENISHMENT_MATCHED_GAIN_MICRO',0))/1_000_000.0,
            'r247RemainingRepairLots':[{k:v for k,v in x.items()} for x in self.repairLots if float(x['remaining'])>EPS][:200],
            'r247ServiceAuthorityClaimCurrent':float(self._service_claim()),
            'r247AvailableContinuationCredit':float(self._available_expand_risk_credit()),
            'r247AvailableCoreServiceAuthority':float(self._available_core_service_authority()),
            'r247OrdinaryActiveMintedCreditPool':float(self.ordinaryActiveMintedCredit),
            'r246Stats':dict(self.r246),'r246Events':self.r246Events[:1600],
        })
        return r

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; tmp=Path(tempfile.mkdtemp(prefix='ms4_r247_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]; cmp=[]
        for mid in mids:
            cr=co[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'; w=cr['winner']
            bsim=r240.HandoffRepairCreditQuarantineSim(tape,1,4)
            try:b=bsim.run_r240(w)
            finally:bsim.close()
            csim=BoundedCoreServiceFavorableRecycleSim(tape,1,4)
            try:c=csim.run_r247(w)
            finally:csim.close()
            rows += [{'marketId':mid,'cell':'MS4_R240_CONTROL','winnerPostHocOnly':w,**b},
                     {'marketId':mid,'cell':'MS4_R247_BOUNDED_CORE_SERVICE_FAVORABLE_RECYCLE','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],
               'bestDelta':c['best']-b['best'],'gapDelta':(c['best']-c['floor'])-(b['best']-b['floor']),
               'fillDelta':c['fillEvents']-b['fillEvents'],'submitDelta':c['submits']-b['submits'],
               'coreService':bool(c['r247CoreActiveMaterialized']),'replSub':c['r247FavorableReplenishmentSubmits'],
               'replFill':c['r247FavorableReplenishmentFills'],'replMatchedGain':c['r247FavorableMatchedFloorGain'],
               'serviceCorrect':bool(c['r247ServiceCorrectnessPass']),'unauthorizedOverflowQty':c.get('unauthorizedOverflowQty',0.0),
               'repairQuotaExcessMax':c.get('repairQuotaExcessMax',0.0)}
            cmp.append(d); print(json.dumps(d,ensure_ascii=False),flush=True)
        C={r['marketId']:r for r in rows if r['cell']=='MS4_R247_BOUNDED_CORE_SERVICE_FAVORABLE_RECYCLE'}
        gates={'correctnessPass':all(float(C[m].get('unauthorizedOverflowQty',0.0))<=EPS and float(C[m].get('repairQuotaExcessMax',0.0))<=EPS and bool(C[m].get('r247ServiceCorrectnessPass')) for m in mids),
               'antiCollapse50pctPass':all(C[m]['fillEvents']>=0.5*next(r['fillEvents'] for r in rows if r['marketId']==m and r['cell']=='MS4_R240_CONTROL') for m in mids),
               'coreServiceExercised':any(bool(C[m]['r247CoreActiveMaterialized']) for m in mids),
               'favorableRecycleExercised':any(int(C[m]['r247FavorableReplenishmentFills'])>0 for m in mids)}
        out={'version':'MS4_R2_47_BOUNDED_CORE_SERVICE_FAVORABLE_RECYCLE_V1','researchOnly':True,'runtimeAuthority':False,
             'markets':mids,'rows':rows,'comparisonVsR240':cmp,'gates':gates,
             'boundary':['R2.40 physical handoff frozen','ordinary Satellite Active continuation credit frozen','one added Core service must reserve existing continuation authority','only added Core-service minted credit is quarantined from generic continuation','actual Repair allocations create one-use favorable pair lots','ordinary Expand first claim','extra favorable re-expand only if ordinary monetary credit is insufficient and repairPrice+expandPrice<=1','pair economics never veto ordinary R2.40 actions','<=180s unchanged','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':gates,'aggregate':{'pnlDelta':sum(x['pnlDelta'] for x in cmp),'floorDelta':sum(x['floorDelta'] for x in cmp),'gapDelta':sum(x['gapDelta'] for x in cmp),'fillDelta':sum(x['fillDelta'] for x in cmp)}},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
