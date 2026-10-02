"""CONTINUATION_PROTOCOL_CROSSOVER_SMOKE4_V1.

Research-only crossed recognition protocol on current V3B realistic-HFT.
Treatment changes ONLY management recognition view of ECONOMIC_CORE carriers.
The target seed is always submitted by the untouched native V3B path first; q_ladder,
Active handoff, physical slots/reservations, payment, fees, latency, queue and max4 remain native.
"""
from __future__ import annotations
import argparse, copy, hashlib, importlib.util, json, math, os, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('b1_h3base', HERE/'run_gpt6_h3a_occupancy_suffix_first_divergence_smoke4_v1.py')
h3=importlib.util.module_from_spec(sp);sp.loader.exec_module(h3)
h4=h3.h4; f=h4.f; EPS=h4.EPS
BRANCHES=('NATIVE','II','FI','IF','FF')
MODE={'NATIVE':(None,None),'II':('I','I'),'FI':('F','I'),'IF':('I','F'),'FF':('F','F')}
TOL=1e-8; ACCOUNT_TOL=1e-7

def r10(x): return round(float(x or 0.0),10)
def stable(x):
    if isinstance(x,dict): return {str(k):stable(v) for k,v in sorted(x.items(),key=lambda kv:str(kv[0]))}
    if isinstance(x,(list,tuple,set)): return [stable(v) for v in (sorted(x) if isinstance(x,set) else x)]
    if isinstance(x,float): return round(x,10)
    if isinstance(x,(str,int,bool)) or x is None:return x
    try:return round(float(x),10)
    except Exception:return str(x)
def digest(x): return hashlib.sha256(json.dumps(stable(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()
def close(a,b,tol=TOL): return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=tol)

def payoff(term,winner=None):
    up=float(term['upQty'])-float(term['buyNotional']); dn=float(term['downQty'])-float(term['buyNotional'])
    out={'upPayoff':up,'downPayoff':dn,'midpoint':0.5*(up+dn),'signedInventoryTiltUP':0.5*(up-dn),
         'floor':min(up,dn),'best':max(up,dn),'buyNotional':float(term['buyNotional']),
         'grossExposure':float(term['upQty'])+float(term['downQty']),'absNetQty':abs(float(term['upQty'])-float(term['downQty']))}
    out['settlementPnlPosthoc']=None if winner is None else (up if str(winner).upper()=='UP' else dn)
    return out

def _role_group(role):
    role=str(role or '')
    if 'REPAIR' in role or role=='ECONOMIC_CORE': return 'REPAIR'
    if 'EXPAND' in role or role=='PROBE_CORE': return 'EXPAND'
    return 'OTHER'

class ProtocolFork(h3.TraceFork):
    def __init__(self,tape,spec,branch):
        self.seedMode,self.futureMode=MODE[branch]
        self.treatmentActive=False; self.seedKey=None; self.futureCoreKeys=set(); self.treatedModes={}
        self.preExistingCoreKeys=set(); self.recognitionEvents=[]; self.recognitionMutationCount=0
        self.currentT=None; self.seedPhysical=None; self.seedAdmissionEvents=[]
        self.roleSubmitCounts={}; self.roleFillCounts={}; self.decisionCalls=0; self.decisionSubmitCalls=0
        self.activityLastT=None; self.activityLastAbs=None; self.absNetIntegralShareSec=0.0; self.peakAbsNet=0.0
        super().__init__(tape,spec,branch)

    def _activity_tick(self,t):
        t=int(t); cur=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        self.peakAbsNet=max(self.peakAbsNet,cur)
        if self.activityLastT is not None and t>=self.activityLastT:
            self.absNetIntegralShareSec += float(self.activityLastAbs or 0.0)*(t-self.activityLastT)/1000.0
        self.activityLastT=t; self.activityLastAbs=cur

    def _all_core_rows(self,side):
        return f.v3b.FifoAggregateResponsibilityLadderV3B._live_role_rows(self,'ECONOMIC_CORE',side)

    def _core_for_side(self,side):
        rows=self._all_core_rows(side)
        if self.branch=='NATIVE' or not self.treatmentActive:
            return rows[0] if rows else None
        before=(float(self.inv['UP']),float(self.inv['DOWN']),float(self.cost),len(self.resp_payment_rows),len(self.slot_key))
        visible=[]; hidden=[]
        for row in rows:
            sid,key,o,role=row; key=str(key); mode=self.treatedModes.get(key)
            if mode=='F' and float(o.get('cum') or 0.0)<=EPS:
                hidden.append({'key':key,'side':str(side),'mode':'F','cum':r10(o.get('cum')),
                               'physicalSlotPresent':key in set(self.slot_key.values()),'cancelRequested':bool(o.get('cancelRequested')),
                               'origin':'SEED' if key==self.seedKey else ('FUTURE_CORE' if key in self.futureCoreKeys else 'UNKNOWN')})
                continue
            visible.append(row)
        after=(float(self.inv['UP']),float(self.inv['DOWN']),float(self.cost),len(self.resp_payment_rows),len(self.slot_key))
        if before!=after:self.recognitionMutationCount+=1
        if hidden:
            self.recognitionEvents.append({'t':None if self.currentT is None else int(self.currentT),'side':str(side),'hidden':hidden,
                                           'allKeys':[str(x[1]) for x in rows],'visibleKeys':[str(x[1]) for x in visible]})
        return visible[0] if visible else None

    def _submit_role(self,t,side,role,p,q,proj,source):
        before_n=int(self.n); before_sub=int(self.submits)
        ok=super()._submit_role(t,side,role,p,q,proj,source)
        if ok and int(self.submits)>before_sub:
            key=f'{side}_{before_n}'; rr=str(role)
            self.roleSubmitCounts[rr]=self.roleSubmitCounts.get(rr,0)+1
            if self.treatmentActive and rr=='ECONOMIC_CORE' and key!=self.seedKey:
                self.futureCoreKeys.add(key)
                if self.branch!='NATIVE': self.treatedModes[key]=str(self.futureMode)
        return ok

    def process(self,t):
        self.currentT=int(t); self._activity_tick(t); before=len(self.fill_accounting)
        out=super().process(t)
        for x in self.fill_accounting[before:]:
            role=str(self.key_role.get(str(x.get('key') or ''),'UNASSIGNED')); self.roleFillCounts[role]=self.roleFillCounts.get(role,0)+1
        self._activity_tick(t); return out

    def _open_one_option(self,t,qv,end):
        self.currentT=int(t); self._activity_tick(t); self.decisionCalls+=1; before_sub=int(self.submits)
        target=int(t)==int(self.spec['t']) and not self.seen
        if target:
            self.seen=True; self._snap_prefix(t,qv); self.prefixLiveKeys=set((self.prefix or {}).get('liveOrders',{})); self.prefixFillAccountingLen=len(self.fill_accounting)
            for side in ('UP','DOWN'):
                for row in self._all_core_rows(side): self.preExistingCoreKeys.add(str(row[1]))
            before_n=int(self.n); before_slot=len(self.slot_history)
            # Critical boundary: untouched native seed path. No q_arm suppression and no forced carrier.
            out=f.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
            newkeys=[]
            for n in range(before_n,int(self.n)):
                for side in ('UP','DOWN'):
                    k=f'{side}_{n}'
                    if k in self.orders:newkeys.append(k)
            core=[k for k in newkeys if str(self.key_role.get(k))=='ECONOMIC_CORE' and k not in getattr(self,'activeKeys',set())]
            self.seedKey=core[0] if len(core)==1 else None
            if self.seedKey:
                self.interventionKeys=[self.seedKey]
                o=self.orders[self.seedKey]
                self.seedPhysical={'key':self.seedKey,'side':str(o.get('side')),'role':str(self.key_role.get(self.seedKey)),'price':r10(o.get('price')),'qty':r10(o.get('qty')),
                                   'qLadderRoute':None if self.q_ladder is None else self.q_ladder.get('route'),'pendingActive':self.q_pending_active is not None}
                if self.branch!='NATIVE': self.treatedModes[self.seedKey]=str(self.seedMode)
            self.seedAdmissionEvents=stable(self.slot_history[before_slot:]); self.intervention={'branch':self.branch,'seedKey':self.seedKey,'seedPhysical':self.seedPhysical,'newKeys':newkeys}
            self.postSubmitState=self._payoff_state(t); self.initialPostSubmitSnapshot=self._snapshot(t); self.globalReady=True; self.treatmentActive=True
            if int(self.submits)>before_sub:self.decisionSubmitCalls+=1
            self._activity_tick(t); return out
        # Entire suffix remains native; only _core_for_side management view is intercepted.
        out=f.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        if int(self.submits)>before_sub:self.decisionSubmitCalls+=1
        self._activity_tick(t); return out

    def cashflow(self,terminal):
        cats={k:{'fills':0,'qty':0.0,'repairQty':0.0,'overflowQty':0.0,'notional':0.0,'upPayoffDelta':0.0,'downPayoffDelta':0.0,'keys':set()} for k in ('SEED','PREFIX_EXISTING','FUTURE_NEW_CORE','OTHER_SUFFIX')}
        pk=set(self.prefixLiveKeys); fk=set(self.futureCoreKeys)
        for x in self.fill_accounting[self.prefixFillAccountingLen:]:
            key=str(x.get('key') or ''); q=float(x.get('confirmedQty') or 0.0); p=float(x.get('executionPriceFromInheritedSubstrate') or 0.0); side=str(x.get('side') or '')
            if q<=EPS: continue
            cat='SEED' if key==self.seedKey else ('PREFIX_EXISTING' if key in pk else ('FUTURE_NEW_CORE' if key in fk else 'OTHER_SUFFIX'))
            z=cats[cat]; z['fills']+=1; z['qty']+=q; z['repairQty']+=float(x.get('matchedRepairQty') or 0.0); z['overflowQty']+=float(x.get('overflowQty') or 0.0); z['notional']+=q*p; z['keys'].add(key)
            z['upPayoffDelta']+=q*(1-p) if side=='UP' else -q*p; z['downPayoffDelta']+=q*(1-p) if side=='DOWN' else -q*p
        for z in cats.values(): z['keys']=sorted(z['keys'])
        p0=self.prefixState or {}; up0=float(p0.get('upPayoff') or 0.0); dn0=float(p0.get('downPayoff') or 0.0)
        up1=float(terminal['upQty'])-float(terminal['buyNotional']); dn1=float(terminal['downQty'])-float(terminal['buyNotional'])
        su=sum(z['upPayoffDelta'] for z in cats.values()); sd=sum(z['downPayoffDelta'] for z in cats.values())
        return {'categories':cats,'residual':{'upPayoff':up1-float(up0)-su,'downPayoff':dn1-float(dn0)-sd}}

    def accounting_checks(self):
        cons=True; over=True
        sums={}
        for x in self.fill_accounting:
            c=float(x.get('confirmedQty') or 0.0); m=float(x.get('matchedRepairQty') or 0.0); o=float(x.get('overflowQty') or 0.0)
            if ('matchedRepairQty' in x or 'overflowQty' in x) and abs(c-m-o)>ACCOUNT_TOL: cons=False
            k=str(x.get('key') or ''); sums[k]=sums.get(k,0.0)+c
        for k,q in sums.items():
            oq=float((self.orders.get(k) or {}).get('qty') or 0.0)
            if q>oq+ACCOUNT_TOL:over=False
        suppressed=[h for e in self.recognitionEvents for h in e['hidden']]
        physical=all(bool(h['physicalSlotPresent']) for h in suppressed)
        preclean=not bool(set(self.treatedModes)&set(self.preExistingCoreKeys))
        return {'confirmedPaymentOverflowConservation':cons,'noOverfill':over,'suppressedStillPhysicallyReserved':physical,
                'recognitionReadOnlyNoAccountingMutation':self.recognitionMutationCount==0,'preExistingCarriersNotRetrofitted':preclean}

    def activity(self,raw):
        self._activity_tick(self.activityLastT or int(self.spec['t']))
        role_sub={g:sum(v for r,v in self.roleSubmitCounts.items() if _role_group(r)==g) for g in ('REPAIR','EXPAND','OTHER')}
        role_fill={g:sum(v for r,v in self.roleFillCounts.items() if _role_group(r)==g) for g in ('REPAIR','EXPAND','OTHER')}
        qcnt=raw.get('quantityLadderCounters') or {}
        livecore=sum(len(self._all_core_rows(s)) for s in ('UP','DOWN'))
        return {'coverageDecisionReceipts':int(self.decisionCalls),'actionDecisionReceipts':int(self.decisionSubmitCalls),
                'holdZeroActionRate':None if self.decisionCalls==0 else 1.0-self.decisionSubmitCalls/self.decisionCalls,
                'submits':int(raw.get('submits') or 0),'fills':int(raw.get('fillEvents') or 0),'alternations':int(raw.get('fillSideAlternations') or 0),
                'repairExpandSubmitComposition':role_sub,'repairExpandFillComposition':role_fill,
                'lifecycleCycleProxyCarrierServicesCompleted':int(qcnt.get('carrierServicesCompleted') or 0),
                'buyNotional':float(raw.get('buyNotional') or 0.0),'grossExposure':float(raw.get('upQty') or 0.0)+float(raw.get('downQty') or 0.0),
                'peakAbsNet':float(self.peakAbsNet),'absNetTimeIntegralShareSec':float(self.absNetIntegralShareSec),
                'pendingReservations':{'liveEconomicCore':int(livecore),'qLadder':self.q_ladder is not None,'pendingActive':self.q_pending_active is not None}}

    def behavior_signature(self,term):
        return digest({'terminal':term,'slotHistory':self.slot_history,'fillAccounting':self.fill_accounting,'paymentRows':self.resp_payment_rows,
                       'responsibilities':self.serializable_lots(),'roleSubmits':self.roleSubmitCounts,'roleFills':self.roleFillCounts,
                       'terminalSnapshot':self._snapshot(self.currentT or self.spec['t'])})

def run_one(tape,s,b,winner):
    sim=ProtocolFork(tape,s,b)
    try:
        raw=sim.run_branch(); term=f.terminal(raw,s); pv=payoff(term,winner); cf=sim.cashflow(term); ac=sim.accounting_checks(); act=sim.activity(raw['raw'])
        seedcat=cf['categories']['SEED']; seedSig={'fills':seedcat['fills'],'qty':r10(seedcat['qty']),'repairQty':r10(seedcat['repairQty']),'overflowQty':r10(seedcat['overflowQty']),
            'notional':r10(seedcat['notional']),'upPayoffDelta':r10(seedcat['upPayoffDelta']),'downPayoffDelta':r10(seedcat['downPayoffDelta'])}
        suppressed=[h for e in sim.recognitionEvents for h in e['hidden']]
        futureSupp=sorted({h['key'] for h in suppressed if h['origin']=='FUTURE_CORE'})
        return {'prefixDigest':raw['prefixDigest'],'triggered':bool(raw['seen']),'seedPhysical':sim.seedPhysical,'seedAdmissionEvents':sim.seedAdmissionEvents,
                'initialPostSubmitSnapshot':sim.initialPostSubmitSnapshot,'seedDirectFillPaymentSignature':seedSig,
                'terminal':term,'payoff':pv,'cashflow':cf,'activity':act,'recognitionEvents':sim.recognitionEvents,
                'futureCoreKeys':sorted(sim.futureCoreKeys),'futureSuppressedDistinctKeys':futureSupp,'futureSuppressedDistinctCount':len(futureSupp),
                'accountingChecks':ac,'ledgerClean':not bool(term['ledgerViolations']),'max4':int(term['maxSlots'])<=4,
                'behaviorLedgerDigest':sim.behavior_signature(term),'preExistingCoreKeys':sorted(sim.preExistingCoreKeys),'treatedModes':dict(sim.treatedModes)}
    finally: sim.close()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--states',required=True); ap.add_argument('--output',required=True); ap.add_argument('--max-states',type=int,default=4)
    a=ap.parse_args(); payload=json.loads(Path(a.states).read_text(encoding='utf-8')); states=list(payload['states'])[:int(a.max_states)]; rows=[]
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='continuation_crossover_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for s in states:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(states,1):
            mid=int(s['marketId']); tape=root/'tapes'/f'{mid}.json.xz'; winner=str(cohort[mid]['winner']).upper(); br={b:run_one(tape,s,b,winner) for b in BRANCHES}
            seed_phys=[stable(br[b]['seedPhysical']) for b in BRANCHES]; seed_adm=[stable(br[b]['seedAdmissionEvents']) for b in BRANCHES]; digs={br[b]['prefixDigest'] for b in BRANCHES}
            common={'prefixParity':len(digs)==1 and None not in digs,'allTriggered':all(br[b]['triggered'] for b in BRANCHES),
                    'seedSideRolePriceQtyParity':len({digest(x) for x in seed_phys})==1 and seed_phys[0] is not None,
                    'seedPhysicalAdmissionParity':len({digest(x) for x in seed_adm})==1,
                    'allLedgerCleanExactFIFO':all(br[b]['ledgerClean'] for b in BRANCHES),'allMax4':all(br[b]['max4'] for b in BRANCHES),
                    'accountingIdentityAndConservation':all(all(br[b]['accountingChecks'].values()) for b in BRANCHES),
                    'lineageCashflowCloses':all(max(abs(float(br[b]['cashflow']['residual']['upPayoff'])),abs(float(br[b]['cashflow']['residual']['downPayoff'])))<=ACCOUNT_TOL for b in BRANCHES),
                    'IIequalsNATIVEFullBehaviorLedgerParity':br['II']['behaviorLedgerDigest']==br['NATIVE']['behaviorLedgerDigest']}
            seed_same_I_F=br['II']['seedDirectFillPaymentSignature']==br['FI']['seedDirectFillPaymentSignature'] and br['IF']['seedDirectFillPaymentSignature']==br['FF']['seedDirectFillPaymentSignature']
            exercised={'IF_futureF_distinct':br['IF']['futureSuppressedDistinctCount'],'FF_futureF_distinct':br['FF']['futureSuppressedDistinctCount'],
                       'persistentProtocolExercised':br['IF']['futureSuppressedDistinctCount']>=2 and br['FF']['futureSuppressedDistinctCount']>=2}
            y={b:float(br[b]['payoff']['settlementPnlPosthoc']) for b in BRANCHES}; dI=y['FI']-y['II']; dF=y['FF']-y['IF']; gamma=dF-dI
            rankI=0 if abs(dI)<=TOL else (1 if dI>0 else -1); rankF=0 if abs(dF)<=TOL else (1 if dF>0 else -1); reversal=rankI!=0 and rankF!=0 and rankI==-rankF
            # Anti-collapse flags compare better-vs-worse within each seed contrast; no promotion if activity drops materially drive apparent gain.
            conf=[]
            for label,left,right,delta in [('I_CONT','II','FI',dI),('F_CONT','IF','FF',dF)]:
                if abs(delta)<=TOL:continue
                better=right if delta>0 else left; worse=left if delta>0 else right; A=br[better]['activity']; W=br[worse]['activity']
                collapse=(A['fills']<W['fills'] or A['submits']<W['submits'] or A['grossExposure']+TOL<W['grossExposure'] or A['buyNotional']+TOL<W['buyNotional'])
                if collapse:conf.append({'contrast':label,'better':better,'worse':worse,'reason':'activity/exposure lower in better branch'})
            activity_confounded=bool(conf)
            checks=dict(common); checks['seedDirectSignatureComparableWithinContinuation']=bool(seed_same_I_F)
            row={'marketId':mid,'t':int(s['t']),'researchStratum':s.get('researchStratum'),'stateSpec':s,'checks':checks,'correctnessPass':all(common.values()),
                 'exercise':exercised,'estimand':{'Y':y,'deltaI':dI,'deltaF':dF,'Gamma':gamma,'terminalRankReversal':reversal},
                 'seedDirectSignatureSameForCleanInterpretation':seed_same_I_F,'activityExposureConfounded':activity_confounded,'activityConfoundDetails':conf,'branches':br}
            rows.append(row); (outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(states),'marketId':mid,'stratum':s.get('researchStratum'),'correct':row['correctnessPass'],'exercise':exercised,'dI':round(dI,8),'dF':round(dF,8),'Gamma':round(gamma,8),'reversal':reversal,'activityConfounded':activity_confounded},ensure_ascii=False),flush=True)
    all_correct=all(r['correctnessPass'] for r in rows); clean_w=[r for r in rows if r['correctnessPass'] and r['exercise']['persistentProtocolExercised'] and r['seedDirectSignatureSameForCleanInterpretation'] and r['estimand']['terminalRankReversal'] and not r['activityExposureConfounded']]
    exercised=[r for r in rows if r['correctnessPass'] and r['exercise']['persistentProtocolExercised']]
    any_inter=any(abs(float(r['estimand']['Gamma']))>TOL for r in exercised)
    if not all_correct: verdict='CORRECTNESS_FAIL'
    elif len(clean_w)>=2: verdict='SUPPORT_CONTINUATION_POLICY_DEPENDENT_VALUE'
    elif len(clean_w)==1 or any_inter: verdict='INCONCLUSIVE'
    elif exercised: verdict='DOWNGRADE_B1_CONTINUATION_PROTOCOL_DEPENDENCE'
    else: verdict='NOT_EXERCISED'
    out={'version':'CONTINUATION_PROTOCOL_CROSSOVER_SMOKE4_V1_20260908','researchOnly':True,'runtimeAuthority':False,'rows':rows,
         'allCorrectnessPass':all_correct,'summary':{'markets':[r['marketId'] for r in rows],'runs':len(rows)*5,'exercisedMarkets':[r['marketId'] for r in exercised],
         'cleanRankReversalWitnesses':[r['marketId'] for r in clean_w],'activityExposureConfoundedMarkets':[r['marketId'] for r in rows if r['activityExposureConfounded']]},'verdict':verdict,
         'boundary':['recognition modifies management view only','seed submitted by untouched native V3B path before treatment activation','submitted/live/cancel-pending carriers retain physical slot/reservation','q_ladder and Active handoff remain native','no false fill/payment/credit/protection','pre-existing carriers never retroactively treated','I is native immediate recognition; F hides treated ECONOMIC_CORE only until confirmed fill','future policy applies only to new ECONOMIC_CORE births after seed','no fixed seconds/window rule','realistic HFT/no dream fill','max4/<=180s/venue minimum/fees/latency/queue model inherited unchanged','consumed development H100 only'],
         'sha256':{'runner':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'states':hashlib.sha256(Path(a.states).read_bytes()).hexdigest(),'bundle':hashlib.sha256(Path(a.bundle).read_bytes()).hexdigest()}}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allCorrectnessPass':all_correct,'verdict':verdict,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
