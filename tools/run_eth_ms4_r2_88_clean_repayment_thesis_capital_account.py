from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_78_persistent_intent_thesis_cycle_capital as r278
r275=r278.r275; r264=r278.r264; v2=r278.v2; EPS=1e-9

class CleanRepaymentThesisCapitalAccountSim(r278.PersistentIntentThesisCycleCapitalSim):
    ACCOUNT_CAP=1.0
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r288=Counter();self.r288Events=[]
        self.accountBalance=0.0
        self.accountReservations={}
        self.accountRearmKeys=set()
        self.accountLien=0.0;self.accountLienGen=None;self.accountLienSide=None
        self.accountEquityPeak=0.0;self.accountConservationErrorMax=0.0
        self.accountActivePaidQty=0.0;self.accountPassivePaidQty=0.0
        self._r288PendingCompletions=[]

    # Disable historical-token behavior while retaining exact cycle FIFO accounting.
    def _mint_token_if_qualified(self,t,z):
        if z.get('r288CompletionQueued') or z.get('r288CompletionProcessed') or float(z.get('remaining') or 0.0)>EPS:return
        z['completedAt']=int(t);z['r288CompletionQueued']=True
        self.r275['CYCLE_COMPLETED']+=1;self._r288PendingCompletions.append(int(z['trancheId']))

    def _expire_stale_tokens(self,t):return
    def _available_token(self):return None

    def _account_outstanding_principal(self):
        return sum(float(z.get('riskPrincipal') or 0.0) for z in self.cycleTranches
                   if z.get('accountFunded') and float(z.get('remaining') or 0.0)>EPS)
    def _account_reserved(self):return sum(max(0.0,float(v)) for v in self.accountReservations.values())
    def _account_available(self):return max(0.0,float(self.accountBalance)-self._account_reserved())
    def _account_equity(self):return float(self.accountBalance)+float(self._account_outstanding_principal())
    def _audit_account(self):
        eq=self._account_equity();self.accountEquityPeak=max(self.accountEquityPeak,eq)
        self.accountConservationErrorMax=max(self.accountConservationErrorMax,max(0.0,eq-self.ACCOUNT_CAP),max(0.0,self._account_reserved()-self.accountBalance))

    def _available_expand_risk_credit(self):
        base=float(super()._available_expand_risk_credit())
        if self.accountLien>EPS and self.scopeSide is not None and int(self.scopeGeneration)==int(self.accountLienGen) and str(self.scopeSide)==str(self.accountLienSide):
            return max(0.0,base-float(self.accountLien))
        return base

    def _clear_stale_lien(self,t):
        if self.accountLien<=EPS:return
        valid=(self.scopeSide is not None and int(self.scopeGeneration)==int(self.accountLienGen) and str(self.scopeSide)==str(self.accountLienSide))
        if valid:return
        self.r288Events.append({'t':int(t),'event':'R288_GENERIC_CREDIT_LIEN_SCOPE_RESET','releasedLien':float(self.accountLien),'oldGeneration':self.accountLienGen,'oldSide':self.accountLienSide})
        self.r288['LIEN_SCOPE_RESET']+=1;self.accountLien=0.0;self.accountLienGen=None;self.accountLienSide=None

    def _new_cycle_tranche(self,t,ev):
        before=len(self.cycleTranches);super()._new_cycle_tranche(t,ev)
        if len(self.cycleTranches)<=before:return
        z=self.cycleTranches[-1]
        z['r288FirstRepairAt']=None;z['r288GenericSpendAfterRepair']=0.0;z['r288GenericSpendRows']=[];z['r288CleanTransfer']=None
        if str(z.get('key')) in self.accountRearmKeys:z['accountFunded']=True

    def _try_cycle_rearm(self,t,end):
        thesis=self.intentThesisSide
        if thesis not in ('UP','DOWN'):self.r288['BLOCK_NO_THESIS']+=1;return False
        if self._account_outstanding_principal()>EPS or self._account_reserved()>EPS:self.r288['BLOCK_ACCOUNT_CHAIN_DEPLOYED']+=1;return False
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:self.r288['BLOCK_LATE_180S']+=1;return False
        if self.scopeSide is None or str(self.scopeSide)!=str(thesis):self.r288['BLOCK_SCOPE_NOT_THESIS']+=1;return False
        if self._has_stale_scope_reservation():self.r288['BLOCK_STALE_SCOPE']+=1;return False
        if self._last_new_receipt==int(t):self.r288['BLOCK_ORDINARY_ACTION_OWNS_RECEIPT']+=1;return False
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:self.r288['BLOCK_SHARED_CAPACITY']+=1;return False
        cand=self._candidate_from_levels_v8(thesis,'SATELLITE_EXPAND',False)
        if cand is None:self.r288['BLOCK_NO_EXPAND_CANDIDATE']+=1;return False
        p,q,proj,split=cand;risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(thesis,p,q)))
        if risk<=EPS:self.r288['BLOCK_NOT_RISK_BEARING']+=1;return False
        ordinary=float(self._available_expand_risk_credit())
        if ordinary+EPS>=risk:self.r288['BLOCK_ORDINARY_CREDIT_SUFFICIENT']+=1;return False
        if self._account_available()+EPS<risk:self.r288['BLOCK_ACCOUNT_INSUFFICIENT']+=1;return False
        before_n=self.n
        if not self._submit_role_v8(t,thesis,'SATELLITE_EXPAND',p,q,proj,None):self.r288['SUBMIT_BLOCKED']+=1;return False
        key=f'{thesis}_{before_n}'
        self.riskTrancheKeys.add(key)
        self.riskTrancheMeta[key]={'key':key,'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,
            'submittedAt':int(t),'price':float(p),'qty':float(q),'authorized':float(risk),'held':float(risk),'spent':0.0,'released':0.0,'terminal':None,
            'creditAvailableAtSubmit':ordinary,'repairProgressAtSubmit':int(self.scopeRepairProgressClocks),'kind':'R288_THESIS_ACCOUNT_REARM','intentThesisSide':thesis}
        self.riskTrancheAuthorizedRisk+=risk;self.accountReservations[key]=float(risk);self.accountRearmKeys.add(key);self.r288['REARM_SUBMIT']+=1
        ev={'t':int(t),'event':'R288_THESIS_ACCOUNT_REARM_SUBMIT','key':key,'thesisSide':thesis,'generation':int(self.scopeGeneration),'price':float(p),'qty':float(q),
            'riskAuthorized':float(risk),'accountBalance':float(self.accountBalance),'accountAvailable':float(self._account_available()),'ordinaryCreditAfterLien':ordinary,'lien':float(self.accountLien)}
        self.r288Events.append(ev);self.slot_history.append(ev);self._audit_r247();self._audit_account();return True

    def _completion_by_id(self,tid):return next((z for z in self.cycleTranches if int(z.get('trancheId') or -1)==int(tid)),None)

    def _process_completion(self,t,z):
        if z is None or z.get('r288CompletionProcessed'):return
        z['r288CompletionProcessed']=True
        passive_only=(float(z.get('passivePaidQty') or 0.0)>=float(z.get('qty') or 0.0)-EPS and float(z.get('activePaidQty') or 0.0)<=EPS)
        nonnegative=float(z.get('pairEdge') or 0.0)>=-EPS
        clean=float(z.get('r288GenericSpendAfterRepair') or 0.0)<=EPS
        account_funded=bool(z.get('accountFunded'))
        principal=float(z.get('riskPrincipal') or 0.0)
        room=max(0.0,self.ACCOUNT_CAP-self._account_equity())
        grant=0.0;lien_add=0.0
        if passive_only and nonnegative and clean:
            grant=min(principal,room)
            if grant>EPS:
                # Only overlap that is still currently spendable as generic credit is liened.
                current_generic=float(super()._available_expand_risk_credit())
                lien_add=min(grant,current_generic)
                if self.scopeSide is not None and lien_add>EPS:
                    if self.accountLien>EPS and (int(self.accountLienGen)!=int(self.scopeGeneration) or str(self.accountLienSide)!=str(self.scopeSide)):
                        self.accountLien=0.0
                    self.accountLien+=lien_add;self.accountLienGen=int(self.scopeGeneration);self.accountLienSide=str(self.scopeSide)
                self.accountBalance+=grant
                self.r288['ACCOUNT_REPLENISH']+=1
                self.r288['ACCOUNT_REPLENISH_FROM_ACCOUNT_CYCLE' if account_funded else 'ACCOUNT_SEED_FROM_NATIVE_CYCLE']+=1
        else:
            if not clean:self.r288['OVERLAP_SPEND_NO_FULL_PRINCIPAL_TRANSFER']+=1
            if not passive_only:self.r288['ACTIVE_OR_MIXED_NO_REPLENISH']+=1
            if not nonnegative:self.r288['NEGATIVE_EDGE_NO_REPLENISH']+=1
        z['r288CleanTransfer']=bool(passive_only and nonnegative and clean);z['r288AccountGrant']=grant;z['r288LienAdded']=lien_add
        self.r288Events.append({'t':int(t),'event':'R288_CYCLE_COMPLETION_ACCOUNT_DECISION','trancheId':z['trancheId'],'key':z['key'],'accountFunded':account_funded,
            'passiveOnly':passive_only,'nonnegative':nonnegative,'genericSpendAfterRepair':float(z.get('r288GenericSpendAfterRepair') or 0.0),
            'riskPrincipal':principal,'pairEdge':float(z.get('pairEdge') or 0.0),'grant':grant,'lienAdded':lien_add,'accountBalanceAfter':float(self.accountBalance)})
        self._audit_account()

    def process(self,t):
        pay_counts={int(z['trancheId']):len(z.get('payments') or []) for z in self.cycleTranches};split0=len(self.splitEvents)
        super().process(t);self._clear_stale_lien(t)
        new_split=self.splitEvents[split0:]

        # Spend account capital on actual account-funded risk fill; native scope credit consumption now represents deployment, so matching lien can release.
        for ev in new_split:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));inc=float(ev.get('fillInc') or 0.0)
            if key in self.accountRearmKeys and inc>EPS:
                spend=float(ev.get('overflowRisk') or 0.0);res=float(self.accountReservations.get(key,0.0));self.accountReservations[key]=max(0.0,res-min(res,spend))
                self.accountBalance=max(0.0,float(self.accountBalance)-spend)
                if self.accountLien>EPS:
                    rel=min(self.accountLien,spend);self.accountLien-=rel
                    if self.accountLien<=EPS:self.accountLien=0.0;self.accountLienGen=None;self.accountLienSide=None
                    self.r288['LIEN_RELEASE_ON_ACCOUNT_DEPLOY']+=1
                gen=int(ev.get('generationAtSubmit') or self.scopeGeneration);ob=self.riskRepairObligations.get(gen)
                if ob is not None and float(ob.get('outstanding') or 0.0)>EPS and ob.get('closedAt') is not None:ob['closedAt']=None;ob['closeReason']=None;self.r288['OBLIGATION_REOPEN']+=1
                for z in reversed(self.cycleTranches):
                    if str(z.get('key'))==key and int(z.get('createdAt') or -1)==int(t):z['accountFunded']=True;break
                self.r288['REARM_FILL']+=1;self.r288Events.append({'t':int(t),'event':'R288_THESIS_ACCOUNT_REARM_FILL','key':key,'fillQty':inc,'riskSpend':spend,'accountBalanceAfter':float(self.accountBalance)})

        # Account reservations from zero-fill terminal orders return to balance availability.
        for key in list(self.accountRearmKeys):
            if float(self.accountReservations.get(key,0.0))<=EPS:continue
            meta=self.riskTrancheMeta.get(key,{})
            if meta.get('terminal') is not None:
                rel=float(self.accountReservations[key]);self.accountReservations[key]=0.0;self.r288['RESERVATION_RELEASE_TERMINAL']+=1
                self.r288Events.append({'t':int(t),'event':'R288_ACCOUNT_RESERVATION_RELEASE_TERMINAL','key':key,'released':rel,'status':meta.get('terminal')})

        # Repair payment first marks the tranche as having entered repayment. Active remains valid liability payment but cannot seed account later.
        for z in self.cycleTranches:
            before=pay_counts.get(int(z['trancheId']),0)
            for p in (z.get('payments') or [])[before:]:
                if z.get('r288FirstRepairAt') is None:z['r288FirstRepairAt']=int(p.get('t') or t)
                if z.get('accountFunded'):
                    if bool(p.get('active')):self.accountActivePaidQty+=float(p.get('qty') or 0.0)
                    else:self.accountPassivePaidQty+=float(p.get('qty') or 0.0)

        # Any ordinary generic SATELLITE_EXPAND fill after a tranche's first Repair payment makes full-principal transfer provenance-dirty.
        explicit_keys=set(self.riskTrancheKeys)
        for ev in new_split:
            if ev.get('event')!='ROLE_FILL_SPLIT' or str(ev.get('role'))!='SATELLITE_EXPAND':continue
            key=str(ev.get('key'));risk=float(ev.get('overflowRisk') or 0.0)
            if key in explicit_keys or risk<=EPS:continue
            gen=int(ev.get('generationAtSubmit') or -1)
            for z in self.cycleTranches:
                if int(z.get('generation') or -2)!=gen or float(z.get('remaining') or 0.0)<=EPS:continue
                fr=z.get('r288FirstRepairAt')
                if fr is None or int(t)<int(fr):continue
                z['r288GenericSpendAfterRepair']=float(z.get('r288GenericSpendAfterRepair') or 0.0)+risk
                z['r288GenericSpendRows'].append({'t':int(t),'key':key,'riskSpend':risk})

        pending=list(self._r288PendingCompletions);self._r288PendingCompletions=[]
        for tid in pending:self._process_completion(t,self._completion_by_id(tid))
        self._audit_r247();self._audit_account()

    def run_r288(self,winner):
        r=super().run_r278(winner)
        correct=(bool(r.get('r278CorrectnessPass')) and self.accountConservationErrorMax<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS and float(r.get('unauthorizedOverflowQty',0.0))<=EPS)
        r.update({'r288Version':'MS4_R2_88_CLEAN_REPAYMENT_THESIS_CAPITAL_ACCOUNT_V1','r288Stats':dict(self.r288),'r288Events':self.r288Events[:6000],
            'r288AccountBalance':float(self.accountBalance),'r288AccountReserved':float(self._account_reserved()),'r288AccountOutstandingPrincipal':float(self._account_outstanding_principal()),
            'r288AccountLien':float(self.accountLien),'r288AccountEquityPeak':float(self.accountEquityPeak),'r288AccountConservationErrorMax':float(self.accountConservationErrorMax),
            'r288AccountActivePaidQty':float(self.accountActivePaidQty),'r288AccountPassivePaidQty':float(self.accountPassivePaidQty),
            'r288RearmSubmits':int(self.r288.get('REARM_SUBMIT',0)),'r288RearmFills':int(self.r288.get('REARM_FILL',0)),'r288CorrectnessPass':bool(correct)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r288_'))
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
            s=CleanRepaymentThesisCapitalAccountSim(tape,1,4)
            try:r=s.run_r288(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R264_CONTROL','winnerPostHocOnly':w,**br},{'marketId':m,'cell':'R278_TOKEN_CONTROL','winnerPostHocOnly':w,**qr},{'marketId':m,'cell':'R288_CLEAN_REPAYMENT_THESIS_ACCOUNT','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'intentThesis':r.get('r278IntentThesisSide'),'thesisWinnerAligned':r.get('r278IntentThesisSide')==w,
               'rearmSubmits':int(r.get('r288RearmSubmits') or 0),'rearmFills':int(r.get('r288RearmFills') or 0),'activePaidQty':float(r.get('r288AccountActivePaidQty') or 0.0),'passivePaidQty':float(r.get('r288AccountPassivePaidQty') or 0.0),
               'accountBalance':float(r.get('r288AccountBalance') or 0.0),'accountOutstanding':float(r.get('r288AccountOutstandingPrincipal') or 0.0),'accountLien':float(r.get('r288AccountLien') or 0.0),'accountEquityPeak':float(r.get('r288AccountEquityPeak') or 0.0),
               'pnlDeltaVsR264':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'floorDeltaVsR264':float(r['floor'])-float(br['floor']),'bestDeltaVsR264':float(r['best'])-float(br['best']),
               'pnlDeltaVsR278':float(r['pnlDiagnosticOnly'])-float(qr['pnlDiagnosticOnly']),'floorDeltaVsR278':float(r['floor'])-float(qr['floor']),'bestDeltaVsR278':float(r['best'])-float(qr['best']),
               'fillDeltaVsR264':int(r['fillEvents'])-int(br['fillEvents']),'candidatePnl':float(r['pnlDiagnosticOnly']),'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),
               'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,'correct':bool(r.get('r288CorrectnessPass')),
               'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0)),
               'inheritedActiveFillQty':float(r.get('ms4R2ActiveRepairFillQty') or 0.0)}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_88_CLEAN_REPAYMENT_THESIS_CAPITAL_ACCOUNT_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'accountRearmExercised':any(x['rearmFills']>0 for x in cmp),'inheritedActiveObserved':any(x['inheritedActiveFillQty']>0 for x in cmp),
                      'negativeControl1945898NoRearm':all(x['rearmFills']==0 for x in cmp if x['marketId']==1945898),
                      'dirty1946656NoFullTransferBehavior':all(x['rearmFills']==0 for x in cmp if x['marketId']==1946656)},
             'boundary':['R2.64 early risk/pre-Repair re-expand frozen','persistent thesis from first successful PROBE_CORE submit','full principal transfer only after passive-only nonnegative completed cycle with zero ordinary generic Expand spend after first Repair payment','only current generic available overlap is liened; no pre-escrow','one account-funded chain at a time','inherited Active unchanged and may pay liability but Active/mixed cycle never recharges account','max4','<=180s','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
