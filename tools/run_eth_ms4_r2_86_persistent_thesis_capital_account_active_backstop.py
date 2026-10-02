from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_78_persistent_intent_thesis_cycle_capital as r278
r275=r278.r275; r264=r278.r264; v2=r278.v2; EPS=1e-9

class PersistentThesisCapitalAccountActiveBackstopSim(r278.PersistentIntentThesisCycleCapitalSim):
    """R2.86: replace historical cycle tokens with one persistent thesis capital account.

    - Account starts at zero and has one venue-min quote-risk unit capacity.
    - Existing R2.64 early risk / pre-Repair re-expand remains frozen.
    - Existing inherited bounded Active Repair remains frozen and may pay liability.
    - Passive-only nonnegative explicit-risk cycle may replenish account.
    - Active/mixed/negative cycles never replenish amplification capital.
    - One account-funded thesis-risk chain at a time; no historical coupon stacking.
    - Repair recovery moved into the account is removed from generic spendable credit
      when that credit is still represented in the current scope.
    """
    ACCOUNT_CAP=1.0
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r286=Counter();self.r286Events=[]
        self.accountBalance=0.0
        self.accountReservations={}
        self.accountRearmKeys=set()
        self.accountGenericCreditTransferred=0.0
        self.accountExternalRecoveryTotal=0.0
        self.accountAmplificationBurn=0.0
        self.accountActivePaidQty=0.0
        self.accountPassivePaidQty=0.0
        self.accountEquityPeak=0.0
        self.accountConservationErrorMax=0.0
        self._r286PendingCompletions=[]

    # Disable R2.75/R2.78 token issuance/use. The cycle FIFO ledger itself is retained.
    def _mint_token_if_qualified(self,t,z):
        if z.get('r286CompletionQueued') or z.get('r286CompletionProcessed') or z['remaining']>EPS:return
        z['completedAt']=int(t)
        z['r286CompletionQueued']=True
        self.r275['CYCLE_COMPLETED']+=1
        self._r286PendingCompletions.append(int(z['trancheId']))

    def _expire_stale_tokens(self,t):
        return

    def _available_token(self):
        return None

    def _account_outstanding_principal(self):
        return sum(float(z.get('riskPrincipal') or 0.0) for z in self.cycleTranches
                   if z.get('accountFunded') and float(z.get('remaining') or 0.0)>EPS)

    def _account_reserved(self):
        return sum(max(0.0,float(v)) for v in self.accountReservations.values())

    def _account_available(self):
        return max(0.0,float(self.accountBalance)-self._account_reserved())

    def _account_equity(self):
        # Reservations are holds inside balance, not extra equity.
        return float(self.accountBalance)+float(self._account_outstanding_principal())

    def _audit_account(self):
        eq=self._account_equity();self.accountEquityPeak=max(self.accountEquityPeak,eq)
        self.accountConservationErrorMax=max(self.accountConservationErrorMax,max(0.0,eq-self.ACCOUNT_CAP))
        self.accountConservationErrorMax=max(self.accountConservationErrorMax,max(0.0,self._account_reserved()-self.accountBalance))

    def _try_cycle_rearm(self,t,end):
        # Account is resource only; persistent thesis decides side. Frozen ordinary R2.64 owns receipt first.
        thesis=self.intentThesisSide
        if thesis not in ('UP','DOWN'):self.r286['BLOCK_NO_THESIS']+=1;return False
        if self._account_outstanding_principal()>EPS or self._account_reserved()>EPS:
            self.r286['BLOCK_ACCOUNT_CHAIN_ALREADY_DEPLOYED']+=1;return False
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:self.r286['BLOCK_LATE_180S']+=1;return False
        if self.scopeSide is None or str(self.scopeSide)!=str(thesis):
            self.r286['BLOCK_SCOPE_NOT_THESIS_ALIGNED']+=1;return False
        if self._has_stale_scope_reservation():self.r286['BLOCK_STALE_SCOPE']+=1;return False
        if self._last_new_receipt==int(t):self.r286['BLOCK_ORDINARY_ACTION_OWNS_RECEIPT']+=1;return False
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:self.r286['BLOCK_SHARED_CAPACITY']+=1;return False
        cand=self._candidate_from_levels_v8(thesis,'SATELLITE_EXPAND',False)
        if cand is None:self.r286['BLOCK_NO_EXPAND_CANDIDATE']+=1;return False
        p,q,proj,split=cand
        risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(thesis,p,q)))
        if risk<=EPS:self.r286['BLOCK_NOT_RISK_BEARING']+=1;return False
        ordinary=float(self._available_expand_risk_credit())
        if ordinary+EPS>=risk:self.r286['BLOCK_ORDINARY_CREDIT_SUFFICIENT']+=1;return False
        if self._account_available()+EPS<risk:self.r286['BLOCK_ACCOUNT_CAPITAL_INSUFFICIENT']+=1;return False
        before_n=self.n
        if not self._submit_role_v8(t,thesis,'SATELLITE_EXPAND',p,q,proj,None):
            self.r286['SUBMIT_BLOCKED']+=1;return False
        key=f'{thesis}_{before_n}'
        self.riskTrancheKeys.add(key)
        self.riskTrancheMeta[key]={'key':key,'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,
            'submittedAt':int(t),'price':float(p),'qty':float(q),'authorized':float(risk),
            'held':float(risk),'spent':0.0,'released':0.0,'terminal':None,
            'creditAvailableAtSubmit':ordinary,'repairProgressAtSubmit':int(self.scopeRepairProgressClocks),
            'kind':'R286_THESIS_ACCOUNT_REARM','intentThesisSide':thesis}
        self.riskTrancheAuthorizedRisk+=risk
        self.accountReservations[key]=float(risk);self.accountRearmKeys.add(key)
        self.r286['ACCOUNT_REARM_SUBMIT']+=1
        ev={'t':int(t),'event':'R286_THESIS_ACCOUNT_REARM_SUBMIT','key':key,'thesisSide':thesis,
            'generation':int(self.scopeGeneration),'price':float(p),'qty':float(q),'riskAuthorized':float(risk),
            'accountBalance':float(self.accountBalance),'accountAvailableBefore':float(self._account_available()),
            'ordinaryCredit':ordinary}
        self.r286Events.append(ev);self.slot_history.append(ev);self._audit_r247();self._audit_account();return True

    def _completion_by_id(self,tid):
        return next((z for z in self.cycleTranches if int(z.get('trancheId') or -1)==int(tid)),None)

    def _process_completion(self,t,z):
        if z is None or z.get('r286CompletionProcessed'):return
        z['r286CompletionProcessed']=True
        passive_only=(float(z.get('passivePaidQty') or 0.0)>=float(z.get('qty') or 0.0)-EPS and float(z.get('activePaidQty') or 0.0)<=EPS)
        nonnegative=(float(z.get('pairEdge') or 0.0)>=-EPS)
        escrow=float(z.get('r286ExternalRecoveryEscrow') or 0.0)
        account_funded=bool(z.get('accountFunded'))
        room=max(0.0,self.ACCOUNT_CAP-self._account_equity())
        grant=0.0
        if passive_only and nonnegative:
            grant=min(float(z.get('riskPrincipal') or 0.0),escrow,room)
            if grant>EPS:
                self.accountBalance+=grant;self.r286['ACCOUNT_REPLENISH']+=1
                if account_funded:self.r286['ACCOUNT_REPLENISH_FROM_ACCOUNT_CYCLE']+=1
                else:self.r286['ACCOUNT_SEED_FROM_NATIVE_CYCLE']+=1
        else:
            self.accountAmplificationBurn+=escrow
            if float(z.get('activePaidQty') or 0.0)>EPS:self.r286['ACTIVE_OR_MIXED_COMPLETION_NO_REPLENISH']+=1
            if not nonnegative:self.r286['NEGATIVE_EDGE_COMPLETION_NO_REPLENISH']+=1
        z['r286PassiveOnly']=bool(passive_only);z['r286Nonnegative']=bool(nonnegative);z['r286AccountGrant']=grant
        z['r286AmplificationBurn']=max(0.0,escrow-grant)
        if z['r286AmplificationBurn']>EPS:self.accountAmplificationBurn+=max(0.0,escrow-grant) if passive_only and nonnegative else 0.0
        self.r286Events.append({'t':int(t),'event':'R286_CYCLE_COMPLETION_ACCOUNT_DECISION','trancheId':z['trancheId'],
            'key':z['key'],'accountFunded':account_funded,'passiveOnly':passive_only,'nonnegative':nonnegative,
            'pairEdge':float(z.get('pairEdge') or 0.0),'riskPrincipal':float(z.get('riskPrincipal') or 0.0),
            'externalRecoveryEscrow':escrow,'grant':grant,'accountBalanceAfter':float(self.accountBalance)})
        self._audit_account()

    def process(self,t):
        old_scope=self.scopeSide;old_gen=int(self.scopeGeneration);credit_before=float(self.scopeRiskCreditTotal)
        consumed_before=float(self.scopeRiskCreditConsumed)
        pay_counts={int(z['trancheId']):len(z.get('payments') or []) for z in self.cycleTranches}
        split0=len(self.splitEvents)
        # Use R278 process: R264/R257 native behavior + exact cycle FIFO, but our token hooks are disabled.
        super().process(t)
        new_split=self.splitEvents[split0:]

        # Mark account-funded confirmed risk fills and spend exact realized risk from account balance.
        for ev in new_split:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));inc=float(ev.get('fillInc') or 0.0)
            if key not in self.accountRearmKeys or inc<=EPS:continue
            spend=float(ev.get('overflowRisk') or 0.0)
            res=float(self.accountReservations.get(key,0.0));take=min(res,spend)
            self.accountReservations[key]=max(0.0,res-take)
            self.accountBalance=max(0.0,float(self.accountBalance)-spend)
            self.r286['ACCOUNT_REARM_FILL']+=1
            gen=int(ev.get('generationAtSubmit') or self.scopeGeneration)
            # R257 may reuse a closed generation obligation; reopen for this newly materialized risk.
            ob=self.riskRepairObligations.get(gen)
            if ob is not None and float(ob.get('outstanding') or 0.0)>EPS and ob.get('closedAt') is not None:
                ob['closedAt']=None;ob['closeReason']=None;self.r286['OBLIGATION_REOPEN']+=1
            for z in reversed(self.cycleTranches):
                if str(z.get('key'))==key and int(z.get('createdAt') or -1)==int(t):
                    z['accountFunded']=True;break
            self.r286Events.append({'t':int(t),'event':'R286_THESIS_ACCOUNT_REARM_FILL','key':key,
                'fillQty':inc,'riskSpend':spend,'accountBalanceAfter':float(self.accountBalance)})

        # Release reservation for terminal unfilled remainder.
        for key in list(self.accountRearmKeys):
            if float(self.accountReservations.get(key,0.0))<=EPS:continue
            meta=self.riskTrancheMeta.get(key,{})
            if meta.get('terminal') is not None:
                rel=float(self.accountReservations.get(key,0.0));self.accountReservations[key]=0.0
                self.r286['ACCOUNT_RESERVATION_RELEASE_TERMINAL']+=1
                self.r286Events.append({'t':int(t),'event':'R286_ACCOUNT_RESERVATION_RELEASE_TERMINAL','key':key,'released':rel,'status':meta.get('terminal')})

        # Find cycle payments added this clock and externalize only credit that is not already spent.
        new_pay=[]
        for z in self.cycleTranches:
            before=pay_counts.get(int(z['trancheId']),0)
            for p in (z.get('payments') or [])[before:]:
                rec=float(p.get('qty') or 0.0)*(1.0-float(p.get('price') or 0.0))
                if rec<=EPS:continue
                new_pay.append((z,p,rec))
                if z.get('accountFunded'):
                    if bool(p.get('active')):self.accountActivePaidQty+=float(p.get('qty') or 0.0)
                    else:self.accountPassivePaidQty+=float(p.get('qty') or 0.0)

        if new_pay:
            same_scope=(old_scope is not None and self.scopeSide==old_scope and int(self.scopeGeneration)==old_gen)
            total_rec=sum(x[2] for x in new_pay)
            eligible_total=0.0
            if same_scope:
                credit_delta=max(0.0,float(self.scopeRiskCreditTotal)-credit_before)
                try:service=float(self._service_claim())
                except Exception:service=0.0
                protected=float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk())+service
                removable=max(0.0,float(self.scopeRiskCreditTotal)-protected)
                eligible_total=min(total_rec,credit_delta,removable)
                if eligible_total>EPS:
                    self.scopeRiskCreditTotal=max(protected,float(self.scopeRiskCreditTotal)-eligible_total)
                    self.accountGenericCreditTransferred+=eligible_total
                    self.r286['GENERIC_REPAIR_CREDIT_TRANSFERRED']+=1
            else:
                # Scope transition reset prevents the old Repair recovery from remaining as spendable generic credit.
                eligible_total=total_rec
                self.r286['SCOPE_TRANSITION_EXTERNAL_RECOVERY']+=1
            remain=eligible_total
            for z,p,rec in new_pay:
                take=min(remain,rec);remain-=take
                if take>EPS:
                    z['r286ExternalRecoveryEscrow']=float(z.get('r286ExternalRecoveryEscrow') or 0.0)+take
                    self.accountExternalRecoveryTotal+=take
                if remain<=EPS:break
            self.r286Events.append({'t':int(t),'event':'R286_RISK_REPAIR_RECOVERY_EXTERNALIZED',
                'sameScope':same_scope,'recoveryObserved':total_rec,'recoveryExternalized':eligible_total,
                'creditBefore':credit_before,'creditAfter':float(self.scopeRiskCreditTotal),
                'consumedDelta':max(0.0,float(self.scopeRiskCreditConsumed)-consumed_before)})

        # Completion was identified during the inherited FIFO allocation; decide after this-clock recovery was externalized.
        pending=list(self._r286PendingCompletions);self._r286PendingCompletions=[]
        for tid in pending:self._process_completion(t,self._completion_by_id(tid))
        self._audit_r247();self._audit_account()

    def run_r286(self,winner):
        r=super().run_r278(winner)
        correct=(bool(r.get('r278CorrectnessPass')) and self.accountConservationErrorMax<=EPS and
                 float(r.get('repairQuotaExcessMax',0.0))<=EPS and float(r.get('unauthorizedOverflowQty',0.0))<=EPS)
        r.update({'r286Version':'MS4_R2_86_PERSISTENT_THESIS_CAPITAL_ACCOUNT_ACTIVE_BACKSTOP_V1',
            'r286Stats':dict(self.r286),'r286Events':self.r286Events[:6000],
            'r286AccountBalance':float(self.accountBalance),'r286AccountReserved':float(self._account_reserved()),
            'r286AccountOutstandingPrincipal':float(self._account_outstanding_principal()),
            'r286AccountEquityPeak':float(self.accountEquityPeak),'r286AccountConservationErrorMax':float(self.accountConservationErrorMax),
            'r286GenericCreditTransferred':float(self.accountGenericCreditTransferred),
            'r286ExternalRecoveryTotal':float(self.accountExternalRecoveryTotal),'r286AmplificationBurn':float(self.accountAmplificationBurn),
            'r286AccountActivePaidQty':float(self.accountActivePaidQty),'r286AccountPassivePaidQty':float(self.accountPassivePaidQty),
            'r286RearmSubmits':int(self.r286.get('ACCOUNT_REARM_SUBMIT',0)),'r286RearmFills':int(self.r286.get('ACCOUNT_REARM_FILL',0)),
            'r286CorrectnessPass':bool(correct)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r286_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];cmp=[]
        for m in mids:
            w=co[m]['winner'];tape=tmp/f'{m}.json.xz'
            b=r264.ExecutionRepresentedPreRepairReexpandSim(tape,1,4)
            try:br=b.run_r264(w)
            finally:b.close()
            q=r278.PersistentIntentThesisCycleCapitalSim(tape,1,4)
            try:qr=q.run_r278(w)
            finally:q.close()
            s=PersistentThesisCapitalAccountActiveBackstopSim(tape,1,4)
            try:r=s.run_r286(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},
                     {'marketId':m,'cell':'R278_TOKEN_CONTROL','winnerPostHocOnly':w,**qr},
                     {'marketId':m,'cell':'R286_THESIS_CAPITAL_ACCOUNT_ACTIVE_BACKSTOP','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'intentThesis':r.get('r278IntentThesisSide'),'thesisWinnerAligned':r.get('r278IntentThesisSide')==w,
               'rearmSubmits':int(r.get('r286RearmSubmits') or 0),'rearmFills':int(r.get('r286RearmFills') or 0),
               'activePaidQty':float(r.get('r286AccountActivePaidQty') or 0.0),'passivePaidQty':float(r.get('r286AccountPassivePaidQty') or 0.0),
               'accountBalance':float(r.get('r286AccountBalance') or 0.0),'accountOutstanding':float(r.get('r286AccountOutstandingPrincipal') or 0.0),
               'accountEquityPeak':float(r.get('r286AccountEquityPeak') or 0.0),'creditTransferred':float(r.get('r286GenericCreditTransferred') or 0.0),
               'pnlDeltaVsR264':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),
               'floorDeltaVsR264':float(r['floor'])-float(br['floor']),'bestDeltaVsR264':float(r['best'])-float(br['best']),
               'pnlDeltaVsR278':float(r['pnlDiagnosticOnly'])-float(qr['pnlDiagnosticOnly']),
               'floorDeltaVsR278':float(r['floor'])-float(qr['floor']),'bestDeltaVsR278':float(r['best'])-float(qr['best']),
               'fillDeltaVsR264':int(r['fillEvents'])-int(br['fillEvents']),'candidatePnl':float(r['pnlDiagnosticOnly']),
               'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,
               'correct':bool(r.get('r286CorrectnessPass')),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),
               'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_86_PERSISTENT_THESIS_CAPITAL_ACCOUNT_ACTIVE_BACKSTOP_RESULT_V1','researchOnly':True,'markets':mids,
             'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'accountRearmExercised':any(x['rearmFills']>0 for x in cmp),
                      'activeBackstopObserved':any(x['activePaidQty']>0 for x in cmp),
                      'negativeControl1945898NoRearm':all(x['rearmFills']==0 for x in cmp if x['marketId']==1945898)},
             'boundary':['R2.64 early risk/pre-Repair re-expand frozen','first successful PROBE_CORE submit is persistent thesis identity only','one account, cap one venue-min quote-risk unit, starts zero','no historical token stacking','generic Repair credit transferred out before becoming thesis account capital when represented','Passive-only nonnegative completed risk cycle may replenish account','Active/mixed/negative cycle pays liability but never replenishes account','inherited Active trigger frozen; no new Core-zero-fill rule','Passive ownership not blanket-cancelled for Active','max4','<=180s','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
