from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter,deque

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_57_risk_fill_passive_repair_obligation as r257
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
r247=r257.r247; v2=r257.v2; EPS=1e-9; REPAIR_ROLES=r257.REPAIR_ROLES

WHEEL_INVARIANTS=(
    'fifoQuantityErrorMax','fifoCostErrorMax','fifoPairReserveErrorMax',
    'riskQuantityConservationErrorMax','riskEnvelopeExcessMax',
    'fillOwnerMismatchCount','riskExecutionAboveLimitMax','riskLotDoubleUseQty',
    'invalidPriceCount','duplicateRiskOwnerCount'
)

class MarketWideLossBearingRiskWheelSim(r257.RiskFillPassiveRepairObligationSim):
    """R2.65: decontaminated GPT6 finite risk wheel on the correctness-clean R2.57 kernel.

    Purposefully NOT inherited from GPT6 shared-max4 scheduler.  This isolates the
    market-wide loss-bearing wheel from the same-receipt/shared-quota conflict found
    in GPT6 Synthesis V1 smoke.  Existing R2.47/R2.57 slot/order semantics remain frozen.

    Extra risk authority is market-wide, not generation-reset:
      commitment = pending limit notional + actual unmatched risk principal + locked losses
      free       = max(0, 2 - commitment)

    Only actual physical FIFO pairing releases unmatched principal.  Negative pair edge
    permanently burns wheel capacity; favorable edge never increases the 2-unit envelope.
    A Repair quantity that returns wheel principal is retired from the one-use R2.47
    favorable-lot lane, preventing the same quantity from authorizing both mechanisms.
    """
    EXTRA_RISK_CAPITAL=2.0
    MAX_OPEN_RISK_LEASES=2

    def __init__(self,tape,fanout_limit=1,max_slots=4):
        self.r265=Counter(); self.r265Events=[]
        self.wheelRisk={}
        self.wheelFIFO={s:deque() for s in ('UP','DOWN')}
        self.wheelLossBurn=0.0; self.wheelPairReserve=0.0
        self.wheelChecks={k:0.0 for k in WHEEL_INVARIANTS}
        self._physicalFillOwners=deque()
        self._retireRepairLots=Counter(); self._retireRequested=Counter(); self._retiredThisClock=Counter()
        self._fillClock=None; self._wheelTerminalLogged=set()
        super().__init__(tape,fanout_limit,max_slots)

    def _wheel_event(self,t,event,**kw):
        ev={'t':int(t),'event':event,**kw}; self.r265Events.append(ev); self.slot_history.append(ev)

    def _held_wheel(self,x):
        if x.get('terminal') is not None:return 0.0
        return max(0.0,float(x['qty'])-float(x['filledQty']))*float(x['limitPrice'])

    def _wheel_commitment(self):
        return float(self.wheelLossBurn + sum(self._held_wheel(x)+float(x['unmatchedCost']) for x in self.wheelRisk.values()))

    def _wheel_free(self):
        return max(0.0,self.EXTRA_RISK_CAPITAL-self._wheel_commitment())

    def _wheel_open(self):
        return [x for x in self.wheelRisk.values() if self._held_wheel(x)>EPS or float(x['unmatchedQty'])>EPS]

    def _issued_authority_current(self):
        # Accounting allowance only.  It does not mint native spendable R2.47 credit.
        g=int(self.scopeGeneration)
        return float(sum(self._held_wheel(x)+float(x['filledQty'])*float(x['limitPrice'])
                         for x in self.wheelRisk.values() if int(x['generation'])==g))

    def _audit_r247(self):
        # Preserve R2.47 service conservation while replacing R2.55 generation-local
        # risk-authority audit with the frozen market-wide wheel.
        for x in self.serviceLedger.values():
            err=abs(float(x['authorized'])-float(x['held'])-float(x['spent'])-float(x['released']))
            self.serviceChecks['serviceConservationErrorMax']=max(self.serviceChecks['serviceConservationErrorMax'],err)
        if self.scopeSide is not None:
            committed=float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk())+float(self._service_claim())
            allowance=float(self.scopeRiskCreditTotal)+float(self._issued_authority_current())
            self.serviceChecks['combinedAuthorityExcessMax']=max(
                self.serviceChecks['combinedAuthorityExcessMax'],max(0.0,committed-allowance))
        # R2.55 runner expects this field.  Re-purpose it strictly as wheel-ledger
        # conservation rather than its obsolete per-generation reset policy.
        if hasattr(self,'riskTrancheOverrunMax'):
            for x in self.riskTrancheMeta.values():
                e=abs(float(x['authorized'])-float(x['held'])-float(x['spent'])-float(x['released']))
                self.riskTrancheOverrunMax=max(self.riskTrancheOverrunMax,e)
            self.riskTrancheOverrunMax=max(self.riskTrancheOverrunMax,max(0.0,self._wheel_commitment()-self.EXTRA_RISK_CAPITAL))

    def _available_core_service_authority(self):
        # Delete R2.55's "risk debt => suppress Core service" coupling.  Core rescue
        # keeps exact R2.47 provenance semantics and competes only through native state.
        return r247.BoundedCoreServiceFavorableRecycleSim._available_core_service_authority(self)

    def _try_pre_repair_risk_tranche(self,t,end):
        # Replacement for R2.55's one-per-generation tranche.  No Repair-progress,
        # Floor>=0, pairSum or full-coverage admission gate is introduced.
        if self.scopeSide is None or int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:return False
        if self._has_stale_scope_reservation():return False
        opened=self._wheel_open()
        if len(opened)>=self.MAX_OPEN_RISK_LEASES:
            self.r265['WHEEL_WAIT_OPEN_LEASE_CAP']+=1; return False
        if any(self._held_wheel(x)>EPS for x in opened):
            self.r265['WHEEL_WAIT_PENDING_CARRIER']+=1; return False
        side=str(self.scopeSide)
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:return False
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return False
        p,q,proj,split=cand; charge=float(p)*float(q)
        if not math.isfinite(charge) or charge<=EPS or charge>self._wheel_free()+EPS:
            self.r265['WHEEL_WAIT_FINITE_CAPITAL']+=1; return False
        before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',p,q,proj,None):return False
        key=f'{side}_{before_n}'
        if key in self.wheelRisk:
            self.wheelChecks['duplicateRiskOwnerCount']+=1; raise RuntimeError('duplicate wheel risk owner')
        gen=int(self.scopeGeneration)
        x={'key':key,'generation':gen,'scopeSide':side,'submittedAt':int(t),
           'limitPrice':float(p),'price':float(p),'qty':float(q),'authorized':charge,
           'held':charge,'spent':0.0,'released':0.0,'terminal':None,
           'filledQty':0.0,'actualCost':0.0,'pairedQty':0.0,'unmatchedQty':0.0,'unmatchedCost':0.0}
        self.wheelRisk[key]=x; self.riskTrancheKeys.add(key); self.riskTrancheMeta[key]=x
        # Keep only R2.55 telemetry semantics; this set is NOT an issuance cap here.
        self.riskTrancheGenerationUsed.add(gen)
        self.riskTrancheAuthorizedRisk+=charge
        self.r255['RISK_TRANCHE_SUBMIT']+=1; self.r265['WHEEL_RISK_SUBMIT']+=1
        self._wheel_event(t,'R265_WHEEL_RISK_SUBMIT',key=key,generation=gen,side=side,price=float(p),qty=float(q),
                          charge=charge,wheelCommittedAfter=self._wheel_commitment(),wheelFreeAfter=self._wheel_free(),
                          openLeasesBefore=len(opened),nativeDebtAtDecision=float(self._scope_debt_qty()))
        self._audit_r247(); self._audit_wheel(); return True

    def record_fill(self,t,side,q,p):
        if not self._physicalFillOwners:
            self.wheelChecks['fillOwnerMismatchCount']+=1; raise RuntimeError('native fill without attributed order')
        owner,expected_side,expected_qty=self._physicalFillOwners.popleft()
        if side!=expected_side or abs(float(q)-float(expected_qty))>EPS:
            self.wheelChecks['fillOwnerMismatchCount']+=1; raise RuntimeError('native fill iteration changed')
        if not math.isfinite(p) or p<0 or p>1:
            self.wheelChecks['invalidPriceCount']+=1; raise ValueError('invalid execution price')
        super().record_fill(t,side,q,p)
        risk=self.wheelRisk.get(owner)
        if risk:
            risk['filledQty']+=float(q); risk['actualCost']+=float(q)*float(p)
            self.wheelChecks['riskExecutionAboveLimitMax']=max(
                self.wheelChecks['riskExecutionAboveLimitMax'],max(0.0,float(p)-float(risk['limitPrice'])))
            self.r265['WHEEL_RISK_FILL_EVENTS']+=1
        opposite='DOWN' if side=='UP' else 'UP'; left=float(q)
        while left>EPS and self.wheelFIFO[opposite]:
            prior=self.wheelFIFO[opposite][0]; paid=min(left,float(prior['remaining']))
            old_risk=self.wheelRisk.get(prior['key'])
            edge=paid*(1.0-float(p)-float(prior['price'])); self.wheelPairReserve+=edge
            if old_risk:
                old_risk['pairedQty']+=paid
                old_risk['unmatchedQty']=max(0.0,float(old_risk['unmatchedQty'])-paid)
                old_risk['unmatchedCost']=max(0.0,float(old_risk['unmatchedCost'])-paid*float(prior['price']))
                if self.key_role.get(owner) in REPAIR_ROLES:
                    self._retireRepairLots[owner]+=paid; self._retireRequested[owner]+=paid
            if risk:risk['pairedQty']+=paid
            if old_risk or risk:
                loss=max(0.0,-edge); self.wheelLossBurn+=loss
                self.r265['WHEEL_FIFO_PAYMENT_EVENTS']+=1
                self._wheel_event(t,'R265_WHEEL_FIFO_PAYMENT',oldKey=prior['key'],paymentKey=owner,
                                  qty=paid,entryPrice=float(prior['price']),paymentPrice=float(p),
                                  lockedPairEdge=edge,permanentLossBurn=loss,cumulativeLossBurn=self.wheelLossBurn)
            prior['remaining']-=paid; left-=paid
            if prior['remaining']<=EPS:self.wheelFIFO[opposite].popleft()
        if left>EPS:
            self.wheelFIFO[side].append({'key':owner,'side':side,'price':float(p),'remaining':left,'bornT':int(t)})
            if risk:
                risk['unmatchedQty']+=left; risk['unmatchedCost']+=left*float(p)
        self._wheel_event(t,'R265_PHYSICAL_FILL',key=owner,side=side,qty=float(q),executionPrice=float(p),
                          unmatchedBirthQty=left,riskOwned=bool(risk))
        self._audit_wheel()

    def _clean_lots(self):
        # Run before ordinary R2.47 lot claims.  A Repair portion that physically
        # returned wheel principal cannot also authorize a favorable replenishment.
        for lot in self.repairLots:
            key=lot['sourceKey']
            if int(lot['t'])!=int(self._fillClock or -1) or self._retireRepairLots[key]<=EPS:continue
            take=min(float(lot['remaining']),float(self._retireRepairLots[key]))
            lot['remaining']-=take; self._retireRepairLots[key]-=take; self._retiredThisClock[key]+=take
            self.r265['WHEEL_RETURN_REPAIR_LOT_QTY_MICRO']+=int(round(take*1_000_000))
            self._wheel_event(self._fillClock,'R265_REPAIR_LOT_RETIRED_FOR_WHEEL_RETURN',sourceKey=key,lotId=int(lot['lotId']),qty=take)
        return super()._clean_lots()

    def _audit_wheel(self):
        c=self.wheelChecks
        c['riskEnvelopeExcessMax']=max(c['riskEnvelopeExcessMax'],max(0.0,self._wheel_commitment()-self.EXTRA_RISK_CAPITAL))
        for x in self.wheelRisk.values():
            c['riskQuantityConservationErrorMax']=max(c['riskQuantityConservationErrorMax'],
                abs(float(x['filledQty'])-float(x['pairedQty'])-float(x['unmatchedQty'])))
        for side in ('UP','DOWN'):
            q=sum(float(x['remaining']) for x in self.wheelFIFO[side]); cost=sum(float(x['remaining'])*float(x['price']) for x in self.wheelFIFO[side])
            nq=sum(float(qty) for qty,_ in self.un[side]); nc=sum(float(qty)*float(price) for qty,price in self.un[side])
            c['fifoQuantityErrorMax']=max(c['fifoQuantityErrorMax'],abs(q-nq))
            c['fifoCostErrorMax']=max(c['fifoCostErrorMax'],abs(cost-nc))
        c['fifoPairReserveErrorMax']=max(c['fifoPairReserveErrorMax'],abs(float(self.wheelPairReserve)-float(self.pairReserve)))

    def process(self,t):
        self._fillClock=int(t); self._retireRepairLots.clear(); self._retireRequested.clear(); self._retiredThisClock.clear(); self._physicalFillOwners.clear()
        split_start=len(self.splitEvents)
        for key,o in self.orders.items():
            snap=self.snap(o); inc=max(0.0,float(snap.get('cumExecQty') or 0.0)-float(o.get('cum') or 0.0))
            if inc>EPS:self._physicalFillOwners.append((key,str(o['side']),inc))
        super().process(t)
        minted=Counter()
        for ev in self.splitEvents[split_start:]:
            if ev.get('event')=='ROLE_FILL_SPLIT' and str(ev.get('role')) in REPAIR_ROLES and int(ev.get('generationAtSubmit') or -1)==int(self.scopeGeneration):
                minted[str(ev.get('key'))]+=float(ev.get('repairAllocated') or 0.0)
        for key,requested in self._retireRequested.items():
            missing=max(0.0,min(float(requested),float(minted[key]))-float(self._retiredThisClock[key]))
            self.wheelChecks['riskLotDoubleUseQty']=max(self.wheelChecks['riskLotDoubleUseQty'],missing)
        if self._physicalFillOwners:
            self.wheelChecks['fillOwnerMismatchCount']+=len(self._physicalFillOwners); raise RuntimeError('native fill omitted from attribution')
        # R2.55 terminal reconciliation mutates the same dict object used by wheelRisk.
        for key,x in self.wheelRisk.items():
            if x.get('terminal') is not None and key not in self._wheelTerminalLogged:
                self._wheelTerminalLogged.add(key); self._wheel_event(t,'R265_WHEEL_RISK_TERMINAL',key=key,status=x.get('terminal'),
                    filledQty=float(x['filledQty']),pairedQty=float(x['pairedQty']),unmatchedQty=float(x['unmatchedQty']),released=float(x.get('released') or 0.0))
        self._audit_r247(); self._audit_wheel()

    def run_r265(self,winner):
        r=super().run_r257(winner); self._audit_r247(); self._audit_wheel()
        correct=(bool(r.get('r247ServiceCorrectnessPass')) and all(float(v)<=EPS for v in self.wheelChecks.values())
                 and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS)
        r.update({'r265Version':'MS4_R2_65_MARKETWIDE_LOSS_BEARING_RISK_WHEEL_V1',
                  'r265Stats':dict(self.r265),'r265Events':self.r265Events[:5000],
                  'r265WheelChecks':dict(self.wheelChecks),'r265CorrectnessPass':bool(correct),
                  'wheelRiskLedger':[dict(x) for x in self.wheelRisk.values()],
                  'wheelFifoOutstanding':{s:list(q) for s,q in self.wheelFIFO.items()},
                  'wheelRiskCapital':self.EXTRA_RISK_CAPITAL,'wheelRiskCommitted':self._wheel_commitment(),
                  'wheelRiskFree':self._wheel_free(),'wheelPermanentLossBurn':self.wheelLossBurn,
                  'wheelRiskSubmits':int(self.r265.get('WHEEL_RISK_SUBMIT',0)),
                  'wheelRiskFillEvents':int(self.r265.get('WHEEL_RISK_FILL_EVENTS',0)),
                  'wheelPaymentEvents':int(self.r265.get('WHEEL_FIFO_PAYMENT_EVENTS',0)),
                  'wheelPaidRiskLeases':sum(float(x['pairedQty'])>EPS for x in self.wheelRisk.values()),
                  'wheelFullyPairedRiskLeases':sum(float(x['filledQty'])>EPS and float(x['unmatchedQty'])<=EPS and self._held_wheel(x)<=EPS for x in self.wheelRisk.values())})
        return r


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True)
    a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; tmp=Path(tempfile.mkdtemp(prefix='ms4_r265_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[]; cmp=[]
        for mid in mids:
            w=co[mid]['winner']; tape=tmp/f'{mid}.json.xz'
            s247=r247.BoundedCoreServiceFavorableRecycleSim(tape,1,4)
            try:b247=s247.run_r247(w)
            finally:s247.close()
            s264=r264.ExecutionRepresentedPreRepairReexpandSim(tape,1,4)
            try:b264=s264.run_r264(w)
            finally:s264.close()
            sim=MarketWideLossBearingRiskWheelSim(tape,1,4)
            try:c=sim.run_r265(w)
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R247_CONTROL','winnerPostHocOnly':w,**b247},
                     {'marketId':mid,'cell':'R264_CONTROL','winnerPostHocOnly':w,**b264},
                     {'marketId':mid,'cell':'R265_MARKETWIDE_RISK_WHEEL','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'pnl':float(c['pnlDiagnosticOnly']),'floor':float(c['floor']),'best':float(c['best']),
               'pnlDeltaVsR247':float(c['pnlDiagnosticOnly'])-float(b247['pnlDiagnosticOnly']),
               'floorDeltaVsR247':float(c['floor'])-float(b247['floor']),
               'gapDeltaVsR247':(float(c['best'])-float(c['floor']))-(float(b247['best'])-float(b247['floor'])),
               'pnlDeltaVsR264':float(c['pnlDiagnosticOnly'])-float(b264['pnlDiagnosticOnly']),
               'floorDeltaVsR264':float(c['floor'])-float(b264['floor']),
               'fillDeltaVsR247':int(c['fillEvents'])-int(b247['fillEvents']),'fillDeltaVsR264':int(c['fillEvents'])-int(b264['fillEvents']),
               'fills':int(c['fillEvents']),'submits':int(c['submits']),'wheelSubmits':int(c['wheelRiskSubmits']),
               'wheelFills':int(c['wheelRiskFillEvents']),'wheelPayments':int(c['wheelPaymentEvents']),
               'wheelLossBurn':float(c['wheelPermanentLossBurn']),'wheelCommitted':float(c['wheelRiskCommitted']),
               'correct':bool(c['r265CorrectnessPass']),'repairQuotaExcessMax':float(c.get('repairQuotaExcessMax',0.0))}
            cmp.append(d); print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_65_MARKETWIDE_LOSS_BEARING_RISK_WHEEL_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'wheelExercised':any(x['wheelFills']>0 for x in cmp),
                      'repaymentExercised':any(x['wheelPayments']>0 for x in cmp)},
             'boundary':['R2.47/R2.57 physical and Repair service substrate frozen','GPT6 shared-max4/same-receipt scheduler NOT inherited','market-wide 2 quote-unit extra risk wheel replaces one-per-generation risk issuance','max two open wheel leases','only actual FIFO pairing releases principal','negative pair edge permanently burns wheel capital','favorable pair edge never enlarges wheel','Repair quantity returning wheel principal is retired from R2.47 favorable-lot authority','ordinary native scalar credit unchanged','no Repair-progress/Floor>=0/pairSum admission gate for wheel risk','<=180s unchanged','realistic HFT','no dream fill','no 8781','consumed mechanism evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
