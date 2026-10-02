"""B3_HANDOFF_NATIVE_BUNDLE_ONESHOT_SMOKE4_V1.

Research-only one-shot fork at four frozen same-side/same-role PENDING_ACTIVE
selection-margin seams. A = complete native BOUNDED_ACTIVE; P = complete native
PASSIVE_PRIMARY selected only for the current opportunity; all suffix decisions return
to untouched native policy immediately. No selector, no persistent priority rule.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, math, os, tempfile, zipfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import build_b3_handoff_native_bundle_oneshot_manifest_v1 as mf
from tools import run_b3_handoff_native_bundle_treatment_preflight_v1 as tp
from tools import run_native_selection_margin_prevalence_stagea16_v1 as margin

b2=margin.b2;clock=b2.clock;cross=clock.cross;base=margin.base;v3=b2.v3
EPS=margin.EPS;TOL=1e-8;ACCOUNT_TOL=1e-7
ARMS=('N','A','P','P_REPEAT')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stable(x):return b2.stable(x)
def digest(x):return b2.digest(x)
def close(a,b,tol=TOL):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=tol)
def cert_plain(c):
    if c is None:return None
    return {k:(float.fromhex(v['hex']) if k in {'price','qty'} and isinstance(v,dict) and 'hex' in v else v) for k,v in c.items()}
def exact_ident(a,b):
    if a is None or b is None:return a is b
    if set(a)!=set(b):return False
    for k in a:
        if k in {'price','qty'}:
            if float(a[k]).hex()!=float(b[k]).hex():return False
        elif a[k]!=b[k]:return False
    return True

def open_r0(sim):
    return mf.r0(sim)

def r0_by_id(rows):return {int(x['id']):x for x in rows}

def payoff_state(sim):
    u=float(sim.inv['UP'])-float(sim.cost);d=float(sim.inv['DOWN'])-float(sim.cost)
    return {'U':u,'D':d,'M':0.5*(u+d),'T':0.5*(u-d),'Floor':min(u,d),'Best':max(u,d),'upQty':float(sim.inv['UP']),'downQty':float(sim.inv['DOWN']),'buyNotional':float(sim.cost)}

def fill_pay_delta(x):
    q=float(x.get('confirmedQty') or 0);p=float(x.get('executionPriceFromInheritedSubstrate') or 0);s=str(x.get('side'))
    return ((q*(1-p) if s=='UP' else -q*p),(q*(1-p) if s=='DOWN' else -q*p))

class B3Fork(clock.InstrumentedFork):
    def __init__(self,tape,spec,arm,seam,manifest_row):
        self.b3Arm=arm;self.b3Seam=seam;self.b3Manifest=manifest_row
        self.oneShotCount=0;self.oneShotReceipt=None;self.forkSnapshot=None;self.selectedKey=None
        self.postForkPeakAbsNet=0.0;self.postForkPeakPositiveNet=0.0;self.postForkPeakNegativeNet=0.0;self.postForkActive=False
        super().__init__(tape,spec,'II',arm)
    def _postfork_peak(self):
        if not self.postForkActive:return
        n=float(self.inv['UP'])-float(self.inv['DOWN']);self.postForkPeakAbsNet=max(self.postForkPeakAbsNet,abs(n));self.postForkPeakPositiveNet=max(self.postForkPeakPositiveNet,n);self.postForkPeakNegativeNet=min(self.postForkPeakNegativeNet,n)
    def process(self,t):
        out=super().process(t);self._postfork_peak();return out
    def _commit_passive_once(self,t,qv,end):
        if self.q_pending_active is None or self.q_ladder is None or self.q_ladder.get('route')!='PENDING_ACTIVE':raise RuntimeError('P_OVERRIDE_NO_PENDING_ACTIVE')
        self.q_arm=self._arm_for_open_qty(int(t),qv)
        try:return super(v3.QuantityResponsibilityLadderV3,self)._open_one_option(int(t),qv,int(end))
        finally:self.q_arm=None
    def _freeze_fork(self,phase,t,qv,end):
        before=b2.behavior_state_digest(self)
        a=margin.audit_current_state(self,int(phase),int(t),qv,int(end));after=b2.behavior_state_digest(self)
        ref=a['reference'];A=copy.deepcopy(ref['candidates']['BOUNDED_ACTIVE']);P=copy.deepcopy(ref['candidates']['PASSIVE_PRIMARY'])
        frozenA=cert_plain(self.b3Manifest['rawBundleIdentity']['A']);frozenP=cert_plain(self.b3Manifest['rawBundleIdentity']['P'])
        checks={'locatorHashExact':a['stateHash']==self.b3Manifest['compactLocatorHash'],'observerReadOnly':before==after and a['observerMutationInert'],
                'referenceClean':a['errorsClean'],'ACompleteLegal':A.get('L')=='TRUE' and A.get('A')=='TRUE' and A.get('identity') is not None,
                'PCompleteLegal':P.get('L')=='TRUE' and P.get('A')=='TRUE' and P.get('identity') is not None,
                'AIdentityExact':exact_ident(A.get('identity'),frozenA),'PIdentityExact':exact_ident(P.get('identity'),frozenP),
                'nativePriorityActive':ref.get('priority')=='BOUNDED_ACTIVE','prefixDigestExact':before==self.b3Manifest['behaviorPrefixDigest'],
                'pendingAuthorityExact':self.q_pending_active is not None and self.q_ladder is not None and self.q_ladder.get('route')=='PENDING_ACTIVE',
                'R0Exact':stable(open_r0(self))==stable(self.b3Manifest['R0'])}
        prefix=payoff_state(self);r0=open_r0(self);live=set(str(k) for k in self.slot_key.values())
        self.forkSnapshot={'phaseOrdinal':int(phase),'eventTimestampMs':int(t),'stateHash':a['stateHash'],'behaviorDigest':before,'qvRawRepr':a.get('rawGuardQvRepr'),
                           'reference':ref,'checks':checks,'prefixPayoff':prefix,'R0':r0,'preExistingKeys':sorted(live),
                           'preSubmits':int(self.submits),'preFills':int(self.fills),'preFillAccountingLen':len(self.fill_accounting),'preFillSeqLen':len(self.fill_side_sequence),
                           'prePaymentLen':len(self.resp_payment_rows),'preRespCompleted':int(self.ledger_summary().get('responsibilitiesCompleted') or 0),
                           'preGrossIntegral':float(self.grossIntegral),'preSignedNetIntegral':float(self.signedNetIntegral),'preAbsNetIntegral':float(self.absNetIntegralShareSec),
                           'originalHandoff':copy.deepcopy(self.q_ladder),'pendingActive':copy.deepcopy(self.q_pending_active)}
        return all(checks.values())
    def dispatch_target(self,phase,t,qv,end):
        if self.oneShotCount!=0:raise RuntimeError('ONESHOT_ALREADY_USED')
        if not self._freeze_fork(phase,t,qv,end):raise RuntimeError('FROZEN_FORK_CORRECTNESS_FAIL')
        before_n=int(self.n);before_sub=int(self.submits);before_pay=len(self.resp_payment_rows);before_hist=len(self.slot_history)
        pre_pending=copy.deepcopy(self.q_pending_active);pre_ladder=copy.deepcopy(self.q_ladder)
        if self.b3Arm in {'N','A'}:out=super()._open_one_option(int(t),qv,int(end))
        else:out=self._commit_passive_once(int(t),qv,int(end))
        keys=[]
        for n in range(before_n,int(self.n)):
            for s in ('UP','DOWN'):
                k=f'{s}_{n}'
                if k in self.orders:keys.append(k)
        self.oneShotCount=1;self.selectedKey=keys[0] if len(keys)==1 else None
        candKey='BOUNDED_ACTIVE' if self.b3Arm in {'N','A'} else 'PASSIVE_PRIMARY';expected=self.forkSnapshot['reference']['candidates'][candKey]['identity']
        actual=None
        if self.selectedKey:
            o=self.orders[self.selectedKey];actual={'side':str(o['side']),'role':str(self.key_role.get(self.selectedKey)),'price':float(o['price']),'qty':float(o['qty'])}
            if candKey=='BOUNDED_ACTIVE':actual['targetExpandSide']=expected.get('targetExpandSide')
            else:actual.update({'managed':False,'originResponsibilityId':None,'targetExpandSide':None})
        post_pending=copy.deepcopy(self.q_pending_active);post_ladder=copy.deepcopy(self.q_ladder)
        checks={'exactOneSubmit':int(self.submits)-before_sub==1 and len(keys)==1,'identityExact':exact_ident(actual,expected),'noPaymentAtCommit':len(self.resp_payment_rows)==before_pay,
                'max4':len(self.slot_key)<=4}
        if candKey=='BOUNDED_ACTIVE':checks.update({'nativeActiveCommit':post_pending is None and post_ladder is not None and post_ladder.get('route')=='ACTIVE' and post_ladder.get('activeKey')==self.selectedKey})
        else:checks.update({'unselectedAAuthorityRetained':stable(post_pending)==stable(pre_pending) and stable(post_ladder)==stable(pre_ladder),
                            'noArtificialOwnerBinding':post_ladder is not None and post_ladder.get('activeKey') is None})
        self.oneShotReceipt={'arm':self.b3Arm,'candidateFamily':candKey,'selectedKey':self.selectedKey,'expectedIdentity':expected,'actualIdentity':actual,
                             'slotEvents':stable(self.slot_history[before_hist:]),'prePendingActive':pre_pending,'postPendingActive':post_pending,
                             'preQLadder':pre_ladder,'postQLadder':post_ladder,'checks':checks,'pass':all(checks.values())}
        self.postForkActive=True;self._postfork_peak();return out

def run_arm(tape,spec,seam,manifest_row,winner,arm):
    sim=B3Fork(tape,spec,arm,seam,manifest_row)
    try:
        updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);base.v2.base.ex.advance_to(sim.bt,first)
        end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
        for ordinal,u in enumerate(updates):
            t=int(u[1]);sim.set_phase(ordinal,t);base.v2.base.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);sim._refresh_slots(t);base.v2.base.apply(sim.book,u);qv=base.v2.base.quotes(sim.book)
            if qv:sim._risk_contract_if_needed(t);sim._reanchor_stale(t)
            if ordinal==int(seam['phaseOrdinal']):
                if t!=int(seam['eventTimestampMs']):raise RuntimeError('SEAM_TIMESTAMP_MISMATCH')
                if qv is None:raise RuntimeError('SEAM_NO_QV')
                sim.dispatch_target(ordinal,t,qv,end)
            elif qv:sim._open_one_option(t,qv,end)
            sim._sample_occupancy();sim._postfork_peak()
        fin=clock.finalize(sim,spec,winner);sim._postfork_peak()
        fork=sim.forkSnapshot
        if fork is None or sim.oneShotCount!=1:raise RuntimeError('SEAM_NOT_TRIGGERED')
        terminal=payoff_state(sim)
        # post-fork exact activity
        suffix_fills=sim.fill_accounting[fork['preFillAccountingLen']:]
        seq=sim.fill_side_sequence[fork['preFillSeqLen']:]
        alts=sum(1 for i in range(1,len(seq)) if seq[i]['side']!=seq[i-1]['side'])
        r0ids={int(x['id']) for x in fork['R0']};payrows=sim.resp_payment_rows[fork['prePaymentLen']:]
        r0pay={rid:0.0 for rid in sorted(r0ids)}
        for x in payrows:
            rid=int(x['responsibilityId'])
            if rid in r0pay:r0pay[rid]+=float(x['qty'])
        terminal_lots={int(x['id']):x for x in sim.serializable_lots()}
        r0out={rid:max(0.0,float(terminal_lots.get(rid,{}).get('remainingQty') or 0.0)) for rid in sorted(r0ids)}
        r0completed={rid:bool(terminal_lots.get(rid,{}).get('completedAt') is not None) for rid in sorted(r0ids)}
        completed_post=sum(1 for x in sim.serializable_lots() if x.get('completedAt') is not None and int(x.get('completedAt') or 0)>=int(seam['eventTimestampMs']))
        suffix_activity={'submits':int(sim.submits)-fork['preSubmits'],'fills':int(sim.fills)-fork['preFills'],'fillAccountingLegs':len(suffix_fills),'alternations':alts,
                         'buyNotional':float(sim.cost)-float(fork['prefixPayoff']['buyNotional']),'confirmedShares':(float(sim.inv['UP'])+float(sim.inv['DOWN'])-float(fork['prefixPayoff']['upQty'])-float(fork['prefixPayoff']['downQty'])),
                         'grossExposureTerminal':float(sim.inv['UP'])+float(sim.inv['DOWN']),'peakAbsNet':float(sim.postForkPeakAbsNet),
                         'absNetTimeIntegral':float(sim.absNetIntegralShareSec)-fork['preAbsNetIntegral'],'filledGrossTimeIntegral':float(sim.grossIntegral)-fork['preGrossIntegral'],
                         'signedNetTimeIntegral':float(sim.signedNetIntegral)-fork['preSignedNetIntegral'],'confirmedResponsibilityCompletions':int(completed_post),
                         'terminalOutstandingTotal':float(sim.ledger_summary().get('terminalOutstandingQty') or 0.0),'terminalR0OutstandingTotal':sum(r0out.values()),
                         'terminalPending':{'qLadder':copy.deepcopy(sim.q_ladder),'qPendingActive':copy.deepcopy(sim.q_pending_active),'liveKeys':sorted(str(k) for k in sim.slot_key.values()),'cancelPending':sorted(str(k) for k in sim.slot_key.values() if bool((sim.orders.get(k) or {}).get('cancelRequested')))}}
        # selected carrier execution
        direct=[x for x in suffix_fills if str(x.get('key'))==str(sim.selectedKey)]
        dq=sum(float(x.get('confirmedQty') or 0) for x in direct);dn=sum(float(x.get('confirmedQty') or 0)*float(x.get('executionPriceFromInheritedSubstrate') or 0) for x in direct)
        try:ss=sim.snap(sim.orders[sim.selectedKey]) if sim.selectedKey else None
        except Exception:ss=None
        selected_exec={'key':sim.selectedKey,'submitted':bool(sim.selectedKey),'physicalAdmission':bool(sim.oneShotReceipt and sim.oneShotReceipt['pass']),
                       'confirmedQty':dq,'fillEvents':len(direct),'weightedActualFillPrice':None if dq<=EPS else dn/dq,
                       'fillRows':stable(direct),'explicitFee':0.0,'feeTelemetry':'FROZEN_SIM_HAS_NO_SEPARATE_FEE_COMPONENT_IN_FILL_ACCOUNTING',
                       'terminalSnapshot':stable(ss),'remainingQty':None if not sim.selectedKey else max(0.0,float(sim.orders[sim.selectedKey]['qty'])-float(sim.orders[sim.selectedKey].get('cum') or 0.0))}
        # same original handoff lineage keys from q_events after fork
        orig=fork['originalHandoff'] or {};orig_id=orig.get('originResponsibilityId') or orig.get('responsibilityId');target_side=orig.get('targetExpandSide')
        lineage_keys=set()
        for e in sim.q_events:
            if int(e.get('t') or 0)<int(seam['eventTimestampMs']):continue
            eid=e.get('originResponsibilityId') or e.get('responsibilityId')
            if orig_id is not None and eid is not None and int(eid)==int(orig_id):
                for kk in ('key','passiveKey','activeKey','sourceKey'):
                    if e.get(kk):lineage_keys.add(str(e[kk]))
        prefix_keys=set(fork['preExistingKeys']);selected=str(sim.selectedKey)
        cats={k:{'fills':0,'qty':0.0,'notional':0.0,'Udelta':0.0,'Ddelta':0.0,'keys':set()} for k in ('selected-direct','pre-existing','same-handoff-suffix','other-suffix')}
        for x in suffix_fills:
            key=str(x.get('key'));q=float(x.get('confirmedQty') or 0);p=float(x.get('executionPriceFromInheritedSubstrate') or 0);du,dd=fill_pay_delta(x)
            if key==selected:cat='selected-direct'
            elif key in prefix_keys:cat='pre-existing'
            elif key in lineage_keys:cat='same-handoff-suffix'
            else:cat='other-suffix'
            z=cats[cat];z['fills']+=1;z['qty']+=q;z['notional']+=q*p;z['Udelta']+=du;z['Ddelta']+=dd;z['keys'].add(key)
        for z in cats.values():z['keys']=sorted(z['keys'])
        du_total=terminal['U']-fork['prefixPayoff']['U'];dd_total=terminal['D']-fork['prefixPayoff']['D'];du_cat=sum(z['Udelta'] for z in cats.values());dd_cat=sum(z['Ddelta'] for z in cats.values())
        cash={'categories':cats,'closureResidual':{'U':du_total-du_cat,'D':dd_total-dd_cat},'prefix':fork['prefixPayoff'],'terminal':terminal}
        acc=fin['accountingChecks'];checks={'forkChecks':all(fork['checks'].values()),'commitChecks':bool(sim.oneShotReceipt and sim.oneShotReceipt['pass']),'oneShotExactlyOnce':sim.oneShotCount==1,
             'selectedPhysicalSubmit':bool(sim.selectedKey),'exactFifoAccounting':all(bool(v) for v in acc.values()),'max4':int(fin['terminal']['maxSlots'])<=4,
             'cashflowClosure':abs(cash['closureResidual']['U'])<=ACCOUNT_TOL and abs(cash['closureResidual']['D'])<=ACCOUNT_TOL}
        return {'arm':arm,'fork':fork,'oneShotReceipt':sim.oneShotReceipt,'selectedExecution':selected_exec,'terminalEconomics':terminal,
                'settlementPnlPosthoc':terminal['U'] if str(winner).upper()=='UP' else terminal['D'],'R0Service':{'frozenR0':fork['R0'],'confirmedPaymentByR0':r0pay,'terminalOutstandingByR0':r0out,'completedByR0':r0completed,'originalHandoffId':orig_id,'targetExpandSide':target_side,'lineageKeys':sorted(lineage_keys)},
                'suffixActivityRisk':suffix_activity,'cashflowAttribution':cash,'behaviorLedgerDigest':fin['behaviorLedgerDigest'],'accountingChecks':acc,'checks':checks,'correctnessPass':all(checks.values())}
    finally:sim.close()

def economic_class(A,P,delta):
    du=float(P['terminalEconomics']['U'])-float(A['terminalEconomics']['U']);dd=float(P['terminalEconomics']['D'])-float(A['terminalEconomics']['D'])
    if abs(du)<=delta+TOL and abs(dd)<=delta+TOL:c='NEUTRAL'
    elif du>=-delta-TOL and dd>=-delta-TOL and (du>delta+TOL or dd>delta+TOL):c='P_DOMINANCE'
    elif du<=delta+TOL and dd<=delta+TOL and (du<-delta-TOL or dd<-delta-TOL):c='A_DOMINANCE'
    else:c='TRADEOFF'
    return {'deltaU':du,'deltaD':dd,'deltaM':0.5*(du+dd),'deltaT':0.5*(du-dd),'deltaFloor':float(P['terminalEconomics']['Floor'])-float(A['terminalEconomics']['Floor']),'deltaBest':float(P['terminalEconomics']['Best'])-float(A['terminalEconomics']['Best']),'class':c,'materialityDelta':float(delta)}

def ratio_gate(w,l):
    if float(l)<=TOL:return {'pass':True,'ratio':None,'zeroBase':True}
    r=float(w)/float(l);return {'pass':r>=0.90-TOL,'ratio':r,'zeroBase':False}

def claim_gate(W,L):
    wa=W['suffixActivityRisk'];la=L['suffixActivityRisk'];fills=ratio_gate(wa['fills'],la['fills']);alts=ratio_gate(wa['alternations'],la['alternations']);comp=ratio_gate(wa['confirmedResponsibilityCompletions'],la['confirmedResponsibilityCompletions'])
    wr=W['R0Service'];lr=L['R0Service'];ids=sorted(set(wr['terminalOutstandingByR0'])|set(lr['terminalOutstandingByR0']))
    r0_not_worse=all(float(wr['terminalOutstandingByR0'].get(i,0))<=float(lr['terminalOutstandingByR0'].get(i,0))+ACCOUNT_TOL for i in ids)
    pending_not_worse=float(wa['terminalOutstandingTotal'])<=float(la['terminalOutstandingTotal'])+ACCOUNT_TOL
    buy_drop=float(wa['buyNotional']) < 0.90*float(la['buyNotional'])-TOL if float(la['buyNotional'])>TOL else False
    r0payW=sum(float(x) for x in wr['confirmedPaymentByR0'].values());r0payL=sum(float(x) for x in lr['confirmedPaymentByR0'].values())
    efficiency_ok=(not buy_drop) or (float(wa['confirmedShares'])+TOL>=float(la['confirmedShares']) and r0payW+ACCOUNT_TOL>=r0payL and fills['pass'] and alts['pass'] and comp['pass'])
    checks={'fills90':fills['pass'],'alternations90':alts['pass'],'completion90':comp['pass'],'R0OutstandingNotWorse':r0_not_worse,'terminalLiabilityNotIncreased':pending_not_worse,'buyNotionalDropExplained':efficiency_ok}
    return {'checks':checks,'pass':all(checks.values()),'ratios':{'fills':fills,'alternations':alts,'completion':comp},'buyNotionalDropGt10pct':buy_drop,'R0Payment':{'W':r0payW,'L':r0payL}}

def direct_vector(br):
    z=br['cashflowAttribution']['categories']['selected-direct'];return {'U':float(z['Udelta']),'D':float(z['Ddelta']),'qty':float(z['qty']),'notional':float(z['notional']),'fills':int(z['fills'])}

def aggregate_market(seam,manifest_row,branches):
    A=branches['A'];P=branches['P'];delta=float(manifest_row['deltaMateriality']);econ=economic_class(A,P,delta)
    same_prefix=len({branches[a]['fork']['behaviorDigest'] for a in ARMS})==1
    checks={'allArmsCorrect':all(branches[a]['correctnessPass'] for a in ARMS),'commonForkPrefixExact':same_prefix and branches['A']['fork']['behaviorDigest']==manifest_row['behaviorPrefixDigest'],
            'N_equals_AFullBehaviorLedgerTerminalParity':branches['N']['behaviorLedgerDigest']==branches['A']['behaviorLedgerDigest'] and stable(branches['N']['terminalEconomics'])==stable(branches['A']['terminalEconomics']),
            'P_equals_P_REPEATFullBehaviorLedgerTerminalParity':branches['P']['behaviorLedgerDigest']==branches['P_REPEAT']['behaviorLedgerDigest'] and stable(branches['P']['terminalEconomics'])==stable(branches['P_REPEAT']['terminalEconomics']),
            'allOneShotExactlyOnce':all(branches[a]['checks']['oneShotExactlyOnce'] for a in ARMS),'allSelectedPhysicalSubmit':all(branches[a]['checks']['selectedPhysicalSubmit'] for a in ARMS)}
    correctness=all(checks.values())
    cgP=claim_gate(P,A);cgA=claim_gate(A,P)
    winnerSide='P' if econ['class']=='P_DOMINANCE' else ('A' if econ['class']=='A_DOMINANCE' else None);winningGate=cgP if winnerSide=='P' else (cgA if winnerSide=='A' else None)
    dvA=direct_vector(A);dvP=direct_vector(P);ddu=dvP['U']-dvA['U'];ddd=dvP['D']-dvA['D']
    directNeutral=abs(ddu)<=delta+TOL and abs(ddd)<=delta+TOL
    terminalNeutral=econ['class']=='NEUTRAL'
    compensation={'directPminusA':{'U':ddu,'D':ddd},'terminalPminusA':{'U':econ['deltaU'],'D':econ['deltaD']},'directNeutral':directNeutral,'terminalNeutral':terminalNeutral,
                  'suffixMaterial':abs(econ['deltaU']-ddu)>delta+TOL or abs(econ['deltaD']-ddd)>delta+TOL,
                  'terminalAbsorbedDirect':terminalNeutral and not directNeutral}
    return {'panel':seam['panel'],'marketId':seam['marketId'],'phaseOrdinal':seam['phaseOrdinal'],'eventTimestampMs':seam['eventTimestampMs'],'checks':checks,'correctnessPass':correctness,'exercisePass':checks['allSelectedPhysicalSubmit'],
            'economicOrdering':econ,'claimGates':{'P_over_A':cgP,'A_over_P':cgA,'apparentWinner':winnerSide,'apparentWinnerGatePass':None if winningGate is None else winningGate['pass']},
            'suffixCompensation':compensation,'branches':branches}

def choose_verdict(rows):
    if not all(r['correctnessPass'] for r in rows):return 'CORRECTNESS_STOP'
    if not all(r['exercisePass'] for r in rows):return 'NOT_EXERCISED'
    cls=[r['economicOrdering']['class'] for r in rows];p=cls.count('P_DOMINANCE');a=cls.count('A_DOMINANCE');trade=cls.count('TRADEOFF');neutral=cls.count('NEUTRAL')
    # Strong support requires one same winner W with no material bilateral downside in all four,
    # >=3 dominance witnesses, D/R witness, and claim gates pass on every market for W.
    for W,cname in [('P','P_DOMINANCE'),('A','A_DOMINANCE')]:
        no_down=[];gates=[]
        for r in rows:
            e=r['economicOrdering'];d=e['materialityDelta'];du=e['deltaU'];dd=e['deltaD']
            no_down.append((du>=-d-TOL and dd>=-d-TOL) if W=='P' else (du<=d+TOL and dd<=d+TOL))
            gates.append(r['claimGates']['P_over_A' if W=='P' else 'A_over_P']['pass'])
        witnesses=[r for r in rows if r['economicOrdering']['class']==cname]
        if all(no_down) and len(witnesses)>=3 and any(r['panel'].startswith('D') for r in witnesses) and any(r['panel'].startswith('R') for r in witnesses) and all(gates):
            return 'SCOPED_B3_VALUE_SIGNAL_SUPPORTED'
    if p and a:return 'SCOPED_B3_VALUE_ORDER_HETEROGENEOUS'
    # If a coherent apparent dominance exists but its winner fails anti-collapse/service, surface that first.
    if (p>=2 and all(r['claimGates']['P_over_A']['pass'] for r in rows if r['economicOrdering']['class']=='P_DOMINANCE') is False) or (a>=2 and all(r['claimGates']['A_over_P']['pass'] for r in rows if r['economicOrdering']['class']=='A_DOMINANCE') is False):
        return 'ACTIVITY_MEDIATED_OR_SUPPRESSED'
    if trade>=2:return 'SCOPED_B3_VALUE_ORDER_NON_TOTAL'
    if all(r['suffixCompensation']['terminalNeutral'] for r in rows) and any(r['suffixCompensation']['terminalAbsorbedDirect'] for r in rows):return 'NATIVE_CONTINUATION_ABSORBS_TERMINAL_VALUE'
    if any(r['suffixCompensation']['directNeutral'] and r['suffixCompensation']['suffixMaterial'] for r in rows):return 'SUFFIX_MEDIATED_INCONCLUSIVE'
    # Only reachability/direct execution changes without replicated terminal ordering.
    directdiff=sum(digest(r['branches']['A']['selectedExecution'])!=digest(r['branches']['P']['selectedExecution']) for r in rows)
    if directdiff>=2 and p+a<=2:return 'DIRECT_EXECUTION_ONLY'
    return 'NO_REPLICATED_SCOPED_VALUE_ORDER / INCONCLUSIVE'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--cohort',required=True);ap.add_argument('--manifest',required=True);ap.add_argument('--preflight',required=True);ap.add_argument('--output',required=True);ap.add_argument('--max-markets',type=int,default=4)
    a=ap.parse_args();mp=json.loads(Path(a.manifest).read_text(encoding='utf-8'));pf=json.loads(Path(a.preflight).read_text(encoding='utf-8'))
    if not mp.get('allRawRestorePass') or pf.get('verdict')!='TREATMENT_CONTRACT_PASS':raise RuntimeError('TREATMENT_CONTRACT_BLOCKED')
    co=json.loads(Path(a.cohort).read_text(encoding='utf-8'));specmap={int(x['marketId']):x for x in co['states']};mmap={int(x['marketId']):x for x in mp['rows']};seams=mf.SEAMS[:int(a.max_markets)];rows=[];outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='b3_oneshot_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for s in seams:z.extract(f"tapes/{s['marketId']}.json.xz",root)
        for i,s in enumerate(seams,1):
            mid=s['marketId'];winner=str(cohort[mid]['winner']).upper();br={arm:run_arm(root/'tapes'/f'{mid}.json.xz',specmap[mid],s,mmap[mid],winner,arm) for arm in ARMS};row=aggregate_market(s,mmap[mid],br);rows.append(row)
            (outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(seams),'marketId':mid,'panel':s['panel'],'correct':row['correctnessPass'],'exercise':row['exercisePass'],'class':row['economicOrdering']['class'],'dU':round(row['economicOrdering']['deltaU'],8),'dD':round(row['economicOrdering']['deltaD'],8),'winnerGate':row['claimGates']['apparentWinnerGatePass']},ensure_ascii=False),flush=True)
    verdict=choose_verdict(rows);allc=all(r['correctnessPass'] for r in rows);alle=all(r['exercisePass'] for r in rows)
    out={'version':'B3_HANDOFF_NATIVE_BUNDLE_ONESHOT_SMOKE4_V1_20260908','researchOnly':True,'runtimeAuthority':False,'consumedDevelopmentOnly':True,'rows':rows,'allCorrectnessPass':allc,'allExercisePass':alle,
         'verdict':verdict,'claimScope':'SCOPED_TOTAL_NATIVE_BUNDLE_PRIORITY' if verdict=='SCOPED_B3_VALUE_SIGNAL_SUPPORTED' else None,
         'b5Status':'B5_GENERALIZATION_NULL_NOT_REJECTED' if verdict=='SCOPED_B3_VALUE_SIGNAL_SUPPORTED' else 'B5_NO_GENERALIZABLE_EDGE_REMAINS_PLAUSIBLE','alphaStatus':'NO_ALPHA_PROMOTION',
         'boundary':['fixed four consumed same-side/same-role PENDING_ACTIVE seams only','one-shot A/P current opportunity then immediate native continuation','N/A and P/P_REPEAT are correctness controls, not economic samples','raw native candidate identities frozen before first treatment fork','P uses existing native passive fallback commit; q_pending_active/q_ladder not artificially cleared','no price/qty/owner/responsibility matching','no HOLD/persistent Passive-first/no selector/no direction model','activity/risk are downstream outcomes; no post-treatment matching','winner used only posthoc settlement display, never verdict ordering','realistic HFT/no dream fill/no fresh/no live8781'],
         'sha256':{'runner':sha(Path(__file__)),'manifest':sha(a.manifest),'preflight':sha(a.preflight),'bundle':sha(a.bundle),'cohort':sha(a.cohort),'stageA16Runner':sha(Path(margin.__file__)),'b2Runner':sha(Path(b2.__file__)),'v3bRuntime':sha(Path(b2.v3b.__file__))}}
    op=(outdir/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'allCorrectnessPass':allc,'allExercisePass':alle,'b5Status':out['b5Status']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
