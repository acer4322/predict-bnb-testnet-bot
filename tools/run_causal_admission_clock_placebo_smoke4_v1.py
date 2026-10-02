"""CAUSAL_ADMISSION_CLOCK_PLACEBO_SMOKE4_V1.

Research-only mechanism falsification on the four already-consumed crossover seams.
N = native I recognition + native admission (old II)
T = future F recognition + native admission (old IF), causal same-phase token donor
P = native I recognition + current-phase T count/notional admission envelope
T_SHAM = T with identical instrumentation and no gate

The token transferred to P contains ONLY current logical phase ordinal/timestamp,
available submit count, and total successful local-admission quote-notional envelope.
No T side/price/role/ownership/candidate/future execution information is exposed to P.
"""
from __future__ import annotations
import argparse, copy, hashlib, inspect, json, math, os, tempfile, zipfile
from pathlib import Path
from typing import Any
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools import run_continuation_protocol_crossover_smoke4_v1 as cross

f=cross.f
v3b=f.v3b
base=v3b.base
EPS=cross.EPS
TOL=1e-8
ACCOUNT_TOL=1e-7
TOKEN_KEYS={'phaseOrdinal','eventTimestampMs','availableSubmitCount','quoteNotionalEnvelope'}
ARMS=('N','T','P','T_SHAM')


def stable(x):
    return cross.stable(x)

def digest(x):
    return cross.digest(x)

def close(a,b,tol=TOL):
    return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=tol)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def local_state(sim):
    live=[]; cancel_pending=[]; live_notional=0.0
    for sid,key in sorted(sim.slot_key.items()):
        o=sim.orders.get(key) or {}
        rem=max(0.0,float(o.get('qty') or 0.0)-float(o.get('cum') or 0.0))
        live.append({'slotId':int(sid),'key':str(key),'side':str(o.get('side')),'price':float(o.get('price') or 0.0),'remainingQty':rem,
                     'role':str(sim.key_role.get(key,'UNASSIGNED')),'cancelRequested':bool(o.get('cancelRequested'))})
        live_notional += rem*float(o.get('price') or 0.0)
        if o.get('cancelRequested'): cancel_pending.append(str(key))
    ls=sim.ledger_summary()
    return {
        'slots':len(sim.slot_key),'liveOrders':live,'cancelPendingKeys':cancel_pending,'liveRequestedQuoteNotional':live_notional,
        'qLadder':None if sim.q_ladder is None else stable(sim.q_ladder),'pendingActive':None if sim.q_pending_active is None else stable(sim.q_pending_active),
        'responsibilityOutstandingBySide':stable(ls.get('outstandingBySide') or {}),'responsibilitiesBorn':int(ls.get('responsibilitiesBorn') or 0),
        'responsibilitiesCompleted':int(ls.get('responsibilitiesCompleted') or 0),
        'inventory':{'UP':float(sim.inv['UP']),'DOWN':float(sim.inv['DOWN'])},'cost':float(sim.cost),'n':int(sim.n),'submits':int(sim.submits),
    }


def mutation_core(sim):
    return digest({'slotKey':sim.slot_key,'orders':{k:sim.orders.get(k) for k in sim.slot_key.values()},'qLadder':sim.q_ladder,
                   'qPendingActive':sim.q_pending_active,'n':sim.n,'submits':sim.submits,'inv':sim.inv,'cost':sim.cost,
                   'responsibilities':sim.serializable_lots(),'paymentRows':sim.resp_payment_rows,
                   'marginalCredit':dict(sim.marginal_pair_credit),'marginalSpend':dict(sim.marginal_pair_risk_spend)})


class InstrumentedFork(cross.ProtocolFork):
    """Behavior-inert same-phase submit observer for N/T/T_SHAM."""
    def __init__(self,tape,spec,protocol_branch,arm_label):
        self.armLabel=arm_label
        self.phaseOrdinal=None;self.phaseT=None;self.postSeed=False
        self.phaseSuccessful=[];self.allSuccessful=[];self.mandatoryBypass=[]
        self.grossLastT=None;self.grossLast=0.0;self.grossIntegral=0.0;self.signedNetIntegral=0.0
        self.peakPositiveNet=0.0;self.peakNegativeNet=0.0
        super().__init__(tape,spec,protocol_branch)

    def set_phase(self,ordinal,t):
        self.phaseOrdinal=int(ordinal);self.phaseT=int(t);self.phaseSuccessful=[]

    def exposure_tick(self,t):
        t=int(t);gross=float(self.inv['UP'])+float(self.inv['DOWN']);net=float(self.inv['UP'])-float(self.inv['DOWN'])
        if self.grossLastT is not None and t>=self.grossLastT:
            dt=(t-self.grossLastT)/1000.0;self.grossIntegral += float(self.grossLast)*dt;self.signedNetIntegral += float(self.netLast)*dt
        self.grossLastT=t;self.grossLast=gross;self.netLast=net
        self.peakPositiveNet=max(self.peakPositiveNet,net);self.peakNegativeNet=min(self.peakNegativeNet,net)

    def process(self,t):
        self.exposure_tick(t);out=super().process(t);self.exposure_tick(t);return out

    def _record_success(self,kind,key,side,role,p,q):
        if not self.postSeed or self.phaseOrdinal is None:return
        rec={'phaseOrdinal':int(self.phaseOrdinal),'eventTimestampMs':int(self.phaseT),'kind':str(kind),'key':str(key),'side':str(side),
             'role':str(role),'price':float(p),'qty':float(q),'quoteNotional':float(p)*float(q)}
        self.phaseSuccessful.append(rec);self.allSuccessful.append(rec)

    def _submit_role(self,t,side,role,p,q,proj,source):
        before_n=int(self.n);before_sub=int(self.submits)
        ok=super()._submit_role(t,side,role,p,q,proj,source)
        if ok and int(self.submits)>before_sub:
            key=f'{side}_{before_n}';self._record_success('PASSIVE_OR_CORE',key,side,role,p,q)
        return ok

    def _submit_protected_active_qty(self,t,qv):
        before_n=int(self.n);before_sub=int(self.submits)
        out=super()._submit_protected_active_qty(t,qv)
        if out and int(self.submits)>before_sub:
            key=next((f'{s}_{before_n}' for s in ('UP','DOWN') if f'{s}_{before_n}' in self.orders),None)
            if key:
                o=self.orders[key];self._record_success('BOUNDED_ACTIVE',key,o.get('side'),self.key_role.get(key),o.get('price'),o.get('qty'))
        return out

    def _open_one_option(self,t,qv,end):
        was_seen=bool(self.seen);out=super()._open_one_option(t,qv,end)
        if (not was_seen) and self.seen:self.postSeed=True
        return out

    def sanitized_token(self):
        return {'phaseOrdinal':int(self.phaseOrdinal),'eventTimestampMs':int(self.phaseT),
                'availableSubmitCount':len(self.phaseSuccessful),
                'quoteNotionalEnvelope':sum(float(x['quoteNotional']) for x in self.phaseSuccessful)}


class PlaceboFork(InstrumentedFork):
    """Native-I allocator with a side-effect-free current-phase count/notional gate."""
    def __init__(self,tape,spec):
        self.currentToken=None;self.tokenUsedCount=0;self.tokenUsedNotional=0.0
        self.totalTokenIssuedCount=0;self.totalTokenIssuedNotional=0.0
        self.gateVetoes=[];self.gatePasses=[];self.phaseCandidates=[];self.phaseBypasses=[];self.allPhaseReceipts=[]
        super().__init__(tape,spec,'II','P')

    def set_token(self,token):
        if set(token)!=TOKEN_KEYS:raise RuntimeError(f'token schema leakage: {sorted(token)}')
        if int(token['phaseOrdinal'])!=int(self.phaseOrdinal) or int(token['eventTimestampMs'])!=int(self.phaseT):raise RuntimeError('future/cross-phase token')
        self.currentToken=copy.deepcopy(token);self.tokenUsedCount=0;self.tokenUsedNotional=0.0
        self.totalTokenIssuedCount+=int(token['availableSubmitCount']);self.totalTokenIssuedNotional+=float(token['quoteNotionalEnvelope'])
        self.phaseCandidates=[];self.phaseBypasses=[]

    def _candidate_diag(self,kind,side,role,p,q):
        rec={'phaseOrdinal':int(self.phaseOrdinal),'eventTimestampMs':int(self.phaseT),'kind':str(kind),'side':str(side),'role':str(role),
             'price':float(p),'qty':float(q),'quoteNotional':float(p)*float(q),'stateBefore':local_state(self)}
        self.phaseCandidates.append(rec);return rec

    def _can_spend(self,notional):
        tok=self.currentToken or {'availableSubmitCount':0,'quoteNotionalEnvelope':0.0}
        count_ok=self.tokenUsedCount+1<=int(tok['availableSubmitCount'])
        cash_ok=self.tokenUsedNotional+float(notional)<=float(tok['quoteNotionalEnvelope'])+TOL
        return count_ok,cash_ok

    def _record_veto(self,cand,reason,before,after):
        rec={**cand,'gateReason':str(reason),'token':copy.deepcopy(self.currentToken),'tokenUsedCountBefore':int(self.tokenUsedCount),
             'tokenUsedNotionalBefore':float(self.tokenUsedNotional),'mutationDigestBefore':before,'mutationDigestAfter':after,
             'noPhysicalAccountingMutation':before==after}
        self.gateVetoes.append(rec);return rec

    def _submit_role(self,t,side,role,p,q,proj,source):
        if not self.postSeed:
            return super()._submit_role(t,side,role,p,q,proj,source)
        # Native physical legality immediately before mutation in MinimalPairRoleSim._submit_role.
        if len(self.slot_key)>=self.max_slots:
            return super()._submit_role(t,side,role,p,q,proj,source)
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:return super()._submit_role(t,side,role,p,q,proj,source)
        cand=self._candidate_diag('PASSIVE_OR_CORE',side,role,p,q);notional=float(p)*float(q);count_ok,cash_ok=self._can_spend(notional)
        if not (count_ok and cash_ok):
            before=mutation_core(self);reason='TOKEN_COUNT_EXHAUSTED' if not count_ok else 'CASH_ENVELOPE_OR_MINIMUM_MISMATCH'
            after=mutation_core(self);self._record_veto(cand,reason,before,after);return False
        before_sub=int(self.submits);before_n=int(self.n);ok=cross.ProtocolFork._submit_role(self,t,side,role,p,q,proj,source)
        if ok and int(self.submits)>before_sub:
            self.tokenUsedCount+=1;self.tokenUsedNotional+=notional;key=f'{side}_{before_n}'
            rec={**cand,'key':key,'token':copy.deepcopy(self.currentToken),'tokenUsedCountAfter':int(self.tokenUsedCount),'tokenUsedNotionalAfter':float(self.tokenUsedNotional)}
            self.gatePasses.append(rec);self._record_success('PASSIVE_OR_CORE',key,side,role,p,q)
        return ok

    def _active_preview(self,qv):
        pnd=self.q_pending_active;L=self.q_ladder
        if pnd is None or L is None:return {'submitLegal':False,'reason':'NO_PENDING_ACTIVE'}
        target=self._aggregate_outstanding_expand_side(pnd['targetExpandSide'])
        if target<=EPS:return {'submitLegal':False,'reason':'QUEUE_SATISFIED_RECONCILIATION','mandatoryReconciliation':True}
        side=pnd['side'];role=pnd['role'];ask=float(qv[side]['ask']);limit=round(min(.99,ask+v3b.TICK),10);min_qty=1.0/limit
        source_remaining=float(pnd['sourceRemainingQty']);qty=min(source_remaining,target)
        if qty+EPS<min_qty:return {'submitLegal':False,'reason':'ACTIVE_BELOW_MINIMUM_RECONCILIATION','mandatoryReconciliation':True,'side':side,'role':role,'qty':qty,'price':limit}
        if len(self.slot_key)>=self.max_slots:return {'submitLegal':False,'reason':'PHYSICAL_CAPACITY_BINDING','side':side,'role':role,'qty':qty,'price':limit}
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:return {'submitLegal':False,'reason':'PHYSICAL_CAPACITY_BINDING','side':side,'role':role,'qty':qty,'price':limit}
        return {'submitLegal':True,'side':side,'role':role,'qty':qty,'price':limit,'notional':qty*limit}

    def _submit_protected_active_qty(self,t,qv):
        if not self.postSeed:return super()._submit_protected_active_qty(t,qv)
        pv=self._active_preview(qv)
        if not pv.get('submitLegal'):
            if pv.get('mandatoryReconciliation'):
                rec={'phaseOrdinal':int(self.phaseOrdinal),'eventTimestampMs':int(self.phaseT),'reason':pv.get('reason'),'type':'MANDATORY_RECONCILIATION',
                     'qty':float(pv.get('qty') or 0.0),'notional':float(pv.get('qty') or 0.0)*float(pv.get('price') or 0.0)}
                self.phaseBypasses.append(rec);self.mandatoryBypass.append(rec)
            return super()._submit_protected_active_qty(t,qv)
        cand=self._candidate_diag('BOUNDED_ACTIVE',pv['side'],pv['role'],pv['price'],pv['qty']);count_ok,cash_ok=self._can_spend(pv['notional'])
        if not (count_ok and cash_ok):
            before=mutation_core(self);reason='TOKEN_COUNT_EXHAUSTED' if not count_ok else 'CASH_ENVELOPE_OR_MINIMUM_MISMATCH';after=mutation_core(self)
            self._record_veto(cand,reason,before,after);return False
        before_sub=int(self.submits);before_n=int(self.n);ok=cross.ProtocolFork._submit_protected_active_qty(self,t,qv)
        if ok and int(self.submits)>before_sub:
            self.tokenUsedCount+=1;self.tokenUsedNotional+=float(pv['notional'])
            key=next((f'{s}_{before_n}' for s in ('UP','DOWN') if f'{s}_{before_n}' in self.orders),None)
            rec={**cand,'key':key,'token':copy.deepcopy(self.currentToken),'tokenUsedCountAfter':int(self.tokenUsedCount),'tokenUsedNotionalAfter':float(self.tokenUsedNotional)}
            self.gatePasses.append(rec)
            if key:self._record_success('BOUNDED_ACTIVE',key,pv['side'],pv['role'],pv['price'],pv['qty'])
        return ok

    def end_phase(self,donor_diag):
        tok=self.currentToken or {'phaseOrdinal':self.phaseOrdinal,'eventTimestampMs':self.phaseT,'availableSubmitCount':0,'quoteNotionalEnvelope':0.0}
        unused_count=int(tok['availableSubmitCount'])-int(self.tokenUsedCount);unused_cash=float(tok['quoteNotionalEnvelope'])-float(self.tokenUsedNotional)
        rec={'phaseOrdinal':int(self.phaseOrdinal),'eventTimestampMs':int(self.phaseT),'token':copy.deepcopy(tok),'tokenUsedCount':int(self.tokenUsedCount),
             'tokenUsedNotional':float(self.tokenUsedNotional),'unusedCount':unused_count,'unusedQuoteNotional':unused_cash,
             'pCandidates':copy.deepcopy(self.phaseCandidates),'pBypasses':copy.deepcopy(self.phaseBypasses),'donorSuccessfulDiagnostics':copy.deepcopy(donor_diag),
             'pStateEnd':local_state(self)}
        self.allPhaseReceipts.append(rec);return rec


def step_predecision(sim,u,ordinal):
    t=int(u[1]);sim.set_phase(ordinal,t);base.v2.base.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);sim._refresh_slots(t)
    base.v2.base.apply(sim.book,u);qv=base.v2.base.quotes(sim.book)
    if qv:
        sim._risk_contract_if_needed(t);sim._reanchor_stale(t)
    return qv


def sim_raw(sim):
    ls=sim.ledger_summary();return {
        'submits':int(sim.submits),'fillEvents':int(sim.fills),'upQty':float(sim.inv['UP']),'downQty':float(sim.inv['DOWN']),'buyNotional':float(sim.cost),
        'floor':float(min(sim.inv['UP'],sim.inv['DOWN'])-sim.cost),'best':float(max(sim.inv['UP'],sim.inv['DOWN'])-sim.cost),
        'fillSideAlternations':int(sum(1 for i in range(1,len(sim.fill_side_sequence)) if sim.fill_side_sequence[i]['side']!=sim.fill_side_sequence[i-1]['side'])),
        'quantityLadderCounters':dict(sim.q_counter),'quantityManagedRepairQtyDiagnostic':float(sim.q_managed_repair_qty),
        'quantityManagedOverflowQtyDiagnostic':float(sim.q_managed_overflow_qty),'quantityLedgerSummary':ls,
        'maxSimultaneousSlots':int(sim.max_simultaneous_slots),'roleSubmits':dict(sim.role_submits),'roleFills':dict(sim.role_fills),
    }


def finalize(sim,spec,winner):
    end2=int(sim.meta['lastReceivedMs']);base.v2.base.ex.advance_to(sim.bt,end2);sim.process(end2);sim._refresh_slots(end2);sim._sample_occupancy();sim.exposure_tick(end2)
    rb={'raw':sim_raw(sim),'seen':sim.seen};term=f.terminal(rb,spec);pv=cross.payoff(term,winner);cf=sim.cashflow(term);ac=sim.accounting_checks();act=sim.activity(rb['raw'])
    act['grossFilledExposureTimeIntegralShareSec']=float(sim.grossIntegral);act['signedNetTimeIntegralShareSec']=float(sim.signedNetIntegral)
    act['peakPositiveNet']=float(sim.peakPositiveNet);act['peakNegativeNet']=float(sim.peakNegativeNet)
    ls=sim.ledger_summary();act['confirmedResponsibilitiesCompleted']=int(ls.get('responsibilitiesCompleted') or 0);act['confirmedResponsibilitiesBorn']=int(ls.get('responsibilitiesBorn') or 0)
    return {'terminal':term,'payoff':pv,'cashflow':cf,'activity':act,'accountingChecks':ac,'prefixDigest':sim.prefixDigest,'seedPhysical':sim.seedPhysical,
            'seedAdmissionEvents':sim.seedAdmissionEvents,'seedDirectFillPaymentSignature':{
                'fills':cf['categories']['SEED']['fills'],'qty':round(cf['categories']['SEED']['qty'],10),'repairQty':round(cf['categories']['SEED']['repairQty'],10),
                'overflowQty':round(cf['categories']['SEED']['overflowQty'],10),'notional':round(cf['categories']['SEED']['notional'],10),
                'upPayoffDelta':round(cf['categories']['SEED']['upPayoffDelta'],10),'downPayoffDelta':round(cf['categories']['SEED']['downPayoffDelta'],10)},
            'futureSuppressedDistinctCount':len(sorted({h['key'] for e in sim.recognitionEvents for h in e['hidden'] if h['origin']=='FUTURE_CORE'})),
            'behaviorLedgerDigest':sim.behavior_signature(term),'treatedModes':dict(sim.treatedModes),'preExistingCoreKeys':sorted(sim.preExistingCoreKeys),
            'terminalLocalState':local_state(sim)}


def ratio_ok(p,t):
    if abs(float(t))<=TOL:return abs(float(p))<=TOL, (1.0 if abs(float(p))<=TOL else None)
    r=float(p)/float(t);return 0.90-TOL<=r<=1.10+TOL,r


def classify_phase(receipt,t_state=None):
    tok=receipt['token'];cands=receipt['pCandidates'];used=receipt['tokenUsedCount'];issued=int(tok['availableSubmitCount'])
    if issued>used:
        st=receipt['pStateEnd']
        if int(st['slots'])>=4:return 'PHYSICAL_CAPACITY_BINDING'
        if cands and any(c.get('quoteNotional',0)>float(tok['quoteNotionalEnvelope'])+TOL for c in cands):return 'CASH_ENVELOPE_OR_MINIMUM_MISMATCH'
        if not cands:return 'NO_LEGAL_OPTION_WITH_HEADROOM'
        return 'NATIVE_PRIORITY_OR_LIFECYCLE_DIVERGENCE'
    if issued==0 and cands:return 'NATIVE_PRIORITY_OR_LIFECYCLE_DIVERGENCE'
    return None


def compare_activity(P,T):
    pa=P['activity'];ta=T['activity'];fields={
        'submits':(pa['submits'],ta['submits']),'fills':(pa['fills'],ta['fills']),'alternations':(pa['alternations'],ta['alternations']),
        'buyNotional':(pa['buyNotional'],ta['buyNotional']),'completedCycleCountConfirmed':(pa['confirmedResponsibilitiesCompleted'],ta['confirmedResponsibilitiesCompleted']),
        'grossExposureTerminal':(pa['grossExposure'],ta['grossExposure']),'grossExposureTimeIntegral':(pa['grossFilledExposureTimeIntegralShareSec'],ta['grossFilledExposureTimeIntegralShareSec']),
        'peakAbsNet':(pa['peakAbsNet'],ta['peakAbsNet']),'absNetTimeIntegral':(pa['absNetTimeIntegralShareSec'],ta['absNetTimeIntegralShareSec'])}
    checks={};ratios={}
    for k,(p,t) in fields.items():checks[k],ratios[k]=ratio_ok(p,t)
    ps=P['terminalLocalState'];ts=T['terminalLocalState']
    terminal_pending=(len(ps['liveOrders'])==len(ts['liveOrders']) and len(ps['cancelPendingKeys'])==len(ts['cancelPendingKeys']) and bool(ps['qLadder'])==bool(ts['qLadder']) and bool(ps['pendingActive'])==bool(ts['pendingActive']) and
                      all(close((ps['responsibilityOutstandingBySide'].get(s) or 0),(ts['responsibilityOutstandingBySide'].get(s) or 0),ACCOUNT_TOL) for s in ('UP','DOWN')))
    checks['terminalPendingAndLiabilityState']=terminal_pending
    return {'checks':checks,'ratios':ratios,'pass':all(checks.values()),'signedExposureDiagnostics':{
        'P_signedNetIntegral':pa['signedNetTimeIntegralShareSec'],'T_signedNetIntegral':ta['signedNetTimeIntegralShareSec'],
        'P_peakPositiveNet':pa['peakPositiveNet'],'T_peakPositiveNet':ta['peakPositiveNet'],'P_peakNegativeNet':pa['peakNegativeNet'],'T_peakNegativeNet':ta['peakNegativeNet']}}


def run_market(tape,spec,winner,reference):
    N=InstrumentedFork(tape,spec,'II','N');T=InstrumentedFork(tape,spec,'IF','T');P=PlaceboFork(tape,spec);S=InstrumentedFork(tape,spec,'IF','T_SHAM')
    sims={'N':N,'T':T,'P':P,'T_SHAM':S};phase_receipts=[]
    try:
        updates=sorted(T.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(T.meta['firstReceivedMs'])
        for sim in sims.values():base.v2.base.ex.advance_to(sim.bt,first)
        end=int((T.payload.get('market') or {}).get('window_end_ms') or T.meta['lastReceivedMs'])
        for ordinal,u in enumerate(updates):
            qv={a:step_predecision(sim,u,ordinal) for a,sim in sims.items()}
            # T reaches only this phase's local submit boundary before token transfer. No future HFT event is advanced here.
            if qv['T']:T._open_one_option(int(u[1]),qv['T'],end)
            token=T.sanitized_token()
            # Explicit schema/no-future gate before P sees token.
            if set(token)!=TOKEN_KEYS or int(token['phaseOrdinal'])!=ordinal:raise RuntimeError('token lineage/schema failure')
            P.set_token(token)
            if qv['P']:P._open_one_option(int(u[1]),qv['P'],end)
            if qv['N']:N._open_one_option(int(u[1]),qv['N'],end)
            if qv['T_SHAM']:S._open_one_option(int(u[1]),qv['T_SHAM'],end)
            for sim in sims.values():sim._sample_occupancy()
            pr=P.end_phase(T.phaseSuccessful);pr['classification']=classify_phase(pr);pr['tStateEnd']=local_state(T);phase_receipts.append(pr)
        out={a:finalize(sim,spec,winner) for a,sim in sims.items()}
        # Hard correctness.
        digs={out[a]['prefixDigest'] for a in ARMS};seedphys={digest(out[a]['seedPhysical']) for a in ARMS};seedadm={digest(out[a]['seedAdmissionEvents']) for a in ARMS}
        nref=(reference or {}).get('II') or {};tref=(reference or {}).get('IF') or {}
        all_gate_veto_inert=all(bool(x.get('noPhysicalAccountingMutation')) for x in P.gateVetoes)
        token_lineage=all(set(r['token'])==TOKEN_KEYS and int(r['token']['phaseOrdinal'])==int(r['phaseOrdinal']) for r in phase_receipts)
        p_authorized=len(P.gatePasses)==len(P.allSuccessful) and all(int(x['token']['phaseOrdinal'])==int(x['phaseOrdinal']) for x in P.gatePasses)
        total_used_count=sum(int(r['tokenUsedCount']) for r in phase_receipts);total_issued_count=sum(int(r['token']['availableSubmitCount']) for r in phase_receipts)
        total_used_cash=sum(float(r['tokenUsedNotional']) for r in phase_receipts);total_issued_cash=sum(float(r['token']['quoteNotionalEnvelope']) for r in phase_receipts)
        count_fid=1.0 if total_issued_count==0 and total_used_count==0 else (total_used_count/total_issued_count if total_issued_count else 0.0)
        cash_fid=1.0 if total_issued_cash<=TOL and total_used_cash<=TOL else (total_used_cash/total_issued_cash if total_issued_cash>TOL else 0.0)
        unused_reconciles=all(int(r['unusedCount'])>=0 and float(r['unusedQuoteNotional'])>=-TOL for r in phase_receipts)
        common_checks={
            'commonPrefixParity':len(digs)==1 and None not in digs,'seedSideRolePriceQtyParity':len(seedphys)==1,'seedPhysicalAdmissionParity':len(seedadm)==1,
            'seedRecognitionI':all(out[a]['treatedModes'].get(out[a]['seedPhysical']['key'])=='I' for a in ARMS if out[a]['seedPhysical']),
            'N_equals_old_II_behaviorLedgerParity':bool(nref) and out['N']['behaviorLedgerDigest']==nref.get('behaviorLedgerDigest'),
            'T_equals_old_IF_behaviorLedgerParity':bool(tref) and out['T']['behaviorLedgerDigest']==tref.get('behaviorLedgerDigest'),
            'T_SHAM_equals_T_behaviorLedgerParity':out['T_SHAM']['behaviorLedgerDigest']==out['T']['behaviorLedgerDigest'],
            'allExactFifoLedgerClean':all(not bool(out[a]['terminal']['ledgerViolations']) for a in ARMS),
            'allAccountingIdentityConservation':all(all(out[a]['accountingChecks'].values()) for a in ARMS),
            'allMax4':all(int(out[a]['terminal']['maxSlots'])<=4 for a in ARMS),'noPhantomReservationOrFalseCreditOnPVeto':all_gate_veto_inert,
            'causalTokenLineageCompleteNoFutureLeakage':token_lineage,'everyPDiscretionarySubmitHasSamePhaseToken':p_authorized,
            'PCountCashEnvelopeNeverExceeded':all(int(r['tokenUsedCount'])<=int(r['token']['availableSubmitCount']) and float(r['tokenUsedNotional'])<=float(r['token']['quoteNotionalEnvelope'])+TOL for r in phase_receipts),
            'unusedTokensReconciled':unused_reconciles,
            'mandatoryBypassReconciled':all(set(x)>={'reason','type','qty','notional'} for x in P.mandatoryBypass),
            'cashflowClosure':all(max(abs(float(out[a]['cashflow']['residual']['upPayoff'])),abs(float(out[a]['cashflow']['residual']['downPayoff'])))<=ACCOUNT_TOL for a in ARMS),
        }
        correctness=all(common_checks.values())
        exercise={'T_futureFDistinctCarriers':out['T']['futureSuppressedDistinctCount'],'P_tokenSubmitCarriers':len({x.get('key') for x in P.gatePasses if x.get('key')}),
                  'P_distinctVetoPhases':len({int(x['phaseOrdinal']) for x in P.gateVetoes}),
                  'pass':out['T']['futureSuppressedDistinctCount']>=2 and len({x.get('key') for x in P.gatePasses if x.get('key')})>=2 and len({int(x['phaseOrdinal']) for x in P.gateVetoes})>=2}
        fidelity={'issuedCount':total_issued_count,'usedCount':total_used_count,'countFidelity':count_fid,'issuedQuoteNotional':total_issued_cash,'usedQuoteNotional':total_used_cash,
                  'quoteNotionalFidelity':cash_fid,'pass':count_fid>=0.90-TOL and cash_fid>=0.90-TOL}
        matching=compare_activity(out['P'],out['T']);matching['amountFidelityPass']=fidelity['pass'];matching['pass']=matching['pass'] and fidelity['pass']
        # C/P/R paired payoff vectors.
        def dv(a,b,k):return float(out[a]['payoff'][k])-float(out[b]['payoff'][k])
        keys=('upPayoff','downPayoff','midpoint','signedInventoryTiltUP','floor','best','settlementPnlPosthoc')
        est={'C_T_minus_N':{k:dv('T','N',k) for k in keys},'P_placebo_minus_N':{k:dv('P','N',k) for k in keys},'R_T_minus_P':{k:dv('T','P',k) for k in keys}}
        residual_equiv=abs(est['R_T_minus_P']['upPayoff'])<=0.01+TOL and abs(est['R_T_minus_P']['downPayoff'])<=0.01+TOL
        # first divergence/ununsed token receipts compactly.
        diagnosis=[r for r in phase_receipts if r.get('classification')]
        first=diagnosis[0] if diagnosis else None
        return {'marketId':int(spec['marketId']),'t':int(spec['t']),'researchStratum':spec.get('researchStratum'),'correctnessChecks':common_checks,'correctnessPass':correctness,
                'implementationFeasibility':{'causalCosim':True,'samePhaseTokenTransfer':True,'tokenSchema':sorted(TOKEN_KEYS),'sideEffectFreePVeto':all_gate_veto_inert,
                    'discretionaryNewSubmitHooks':['PASSIVE_OR_CORE via _submit_role','BOUNDED_ACTIVE via _submit_protected_active_qty'],
                    'mandatoryBoundary':'existing fill/cancel/expiry/reservation reconciliation never gated; active no-submit reconciliation bypassed and logged',
                    'executionComparability':'independent deterministic realistic-HFT instances; N/T old behavior-digest parity is hard gate'},
                'exercise':exercise,'tokenFidelity':fidelity,'activityExposureMatching':matching,'estimands':est,'payoffResidualEquivalence':residual_equiv,
                'firstDivergence':first,'divergenceCounts':{c:sum(r.get('classification')==c for r in phase_receipts) for c in ('PHYSICAL_CAPACITY_BINDING','NO_LEGAL_OPTION_WITH_HEADROOM','CASH_ENVELOPE_OR_MINIMUM_MISMATCH','NATIVE_PRIORITY_OR_LIFECYCLE_DIVERGENCE','UNKNOWN_OR_MISSING')},
                'tokenReceipts':phase_receipts,'gateVetoes':P.gateVetoes,'gatePasses':P.gatePasses,'mandatoryBypasses':P.mandatoryBypass,'arms':out}
    finally:
        for sim in sims.values():sim.close()


def feasibility_static():
    src_v3b=inspect.getsource(v3b.FifoAggregateResponsibilityLadderV3B)
    src_base=inspect.getsource(base.MinimalPairRoleSim)
    src_loop=inspect.getsource(base.v3.RoleSeparatedMultiSlotSim.run_v3)
    checks={
        'passiveCoreSubmitCentralized': 'def _submit_role' in src_v3b and 'MinimalPairRoleSim._submit_role' in src_v3b,
        'boundedActiveSubmitCentralized':'def _submit_protected_active_qty' in src_v3b and ('submit_buy_order' in src_v3b or 'submit_sell_order' in src_v3b),
        'nativeLoopSingleDecisionBoundary':'self._open_one_option(t,qv,end)' in src_loop,
        'existingCancelSeparateFromSubmit':hasattr(base.v3.RoleSeparatedMultiSlotSim,'_request_cancel') and base.v3.RoleSeparatedMultiSlotSim._request_cancel is not base.MinimalPairRoleSim._submit_role,
        'placeboOverridesBothSubmitHooks':PlaceboFork._submit_role is not InstrumentedFork._submit_role and PlaceboFork._submit_protected_active_qty is not InstrumentedFork._submit_protected_active_qty,
    }
    return {'checks':checks,'pass':all(checks.values()),'discretionaryMandatoryClassification':{
        'discretionary':['all new PASSIVE/CORE/q_ladder submissions at _submit_role','bounded Active new submission when native active preview is fully legal'],
        'mandatoryNotGated':['existing-order fills','cancel/expiry/stale handling','slot release/reservation reconciliation','active queue-satisfied/below-minimum no-submit reconciliation']}}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--states',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);ap.add_argument('--max-states',type=int,default=4);ap.add_argument('--feasibility-only',action='store_true')
    a=ap.parse_args();feas=feasibility_static()
    if a.feasibility_only:
        op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps({'version':'CAUSAL_ADMISSION_CLOCK_PLACEBO_SMOKE4_V1_FEASIBILITY','feasibility':feas},indent=2),encoding='utf-8');print(json.dumps({'ok':feas['pass'],'feasibility':feas},ensure_ascii=False));return
    if not feas['pass']:raise RuntimeError('IMPLEMENTATION_FEASIBILITY_BLOCKER')
    states_payload=json.loads(Path(a.states).read_text(encoding='utf-8'));states=list(states_payload['states'])[:int(a.max_states)]
    refs=json.loads(Path(a.reference).read_text(encoding='utf-8'));refmap={int(x['marketId']):x['branches'] for x in refs['rows']}
    rows=[];outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='causal_clock_placebo_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for s in states:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(states,1):
            mid=int(s['marketId']);row=run_market(root/'tapes'/f'{mid}.json.xz',s,str(cohort[mid]['winner']).upper(),refmap.get(mid));rows.append(row)
            (outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(states),'marketId':mid,'correct':row['correctnessPass'],'exercise':row['exercise']['pass'],'fidelity':row['tokenFidelity']['pass'],'matched':row['activityExposureMatching']['pass'],
                              'R_UP':round(row['estimands']['R_T_minus_P']['upPayoff'],6),'R_DOWN':round(row['estimands']['R_T_minus_P']['downPayoff'],6),'firstDiv':None if row['firstDivergence'] is None else row['firstDivergence'].get('classification')},ensure_ascii=False),flush=True)
    all_correct=all(r['correctnessPass'] for r in rows);all_ex=all(r['exercise']['pass'] for r in rows);matched=[r for r in rows if r['correctnessPass'] and r['exercise']['pass'] and r['activityExposureMatching']['pass']]
    if not all_correct:verdict='CORRECTNESS_STOP'
    elif not all_ex:verdict='NOT_EXERCISED'
    elif len(matched)<len(rows):verdict='ECONOMIC_SEPARATION_NOT_IDENTIFIED'
    elif all(r['payoffResidualEquivalence'] for r in rows):verdict='CLOCK_SUFFICIENT_ON_TESTED_SEAMS'
    else:
        material=[r for r in matched if not r['payoffResidualEquivalence']]
        verdict='CLOCK_NOT_SUFFICIENT_FOR_PAYOFF' if len(material)>=2 else 'PARTIAL_OR_INCONCLUSIVE'
    out={'version':'CAUSAL_ADMISSION_CLOCK_PLACEBO_SMOKE4_V1_20260908','researchOnly':True,'runtimeAuthority':False,'consumedDevelopmentMechanismFalsification':True,
         'feasibility':feas,'allCorrectnessPass':all_correct,'allExercisePass':all_ex,'matchedMarkets':[r['marketId'] for r in matched],'rows':rows,'verdict':verdict,
         'boundary':['fixed four consumed crossover markets only','seed and prefix native I','T token causal same logical phase only','token contains no side/price/role/ownership/candidate/future data','P uses own native I allocator/candidate/ledger/book','P candidate is whole-order pass or pre-mutation diagnostic veto','no token carryover','max4/<=180s/venue minimum/fees/latency/queue/partial fill/cancel/expiry/stale/exact FIFO unchanged','no dream fill/no Target runtime/no 8781'],
         'sha256':{'runner':sha(Path(__file__)),'states':sha(a.states),'bundle':sha(a.bundle),'reference':sha(a.reference)}}
    op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'allCorrectnessPass':all_correct,'allExercisePass':all_ex,'matchedMarkets':out['matchedMarkets']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
