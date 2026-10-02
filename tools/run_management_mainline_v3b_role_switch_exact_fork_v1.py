from __future__ import annotations
import argparse,copy,hashlib,json,math,os,tempfile,zipfile
from pathlib import Path
from tools import run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS
base=v3b.base
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}
EXPAND_ROLES={'PROBE_CORE','SATELLITE_EXPAND'}
BRANCHES=('NATIVE','NEXT_REPAIR','NEXT_REEXPAND')


def norm(x):
    if isinstance(x,dict):return {str(k):norm(v) for k,v in sorted(x.items(),key=lambda kv:str(kv[0]))}
    if isinstance(x,(list,tuple)):return [norm(v) for v in x]
    if isinstance(x,(str,int,float,bool)) or x is None:return x
    try:return float(x)
    except:return str(x)

def digest(x):
    return hashlib.sha256(json.dumps(norm(x),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def close(a,b,tol=1e-9):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=tol)

class RoleSwitchFork(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape,spec,branch):
        super().__init__(tape)
        self.spec=spec;self.branch=branch;self.seen=False;self.prefix=None;self.prefixDigest=None
        self.intervention=None;self.branchKey=None;self.branchRole=None;self.branchResolution=None
        self.prefixState=None;self.postSubmitState=None

    def _payoff_state(self,t):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);pu=u-c;pd=d-c
        weak=str(self.spec['weakSide']);fav=str(self.spec['expandSide'])
        payoff={'UP':pu,'DOWN':pd}
        return {
            't':int(t),'upPayoff':pu,'downPayoff':pd,'floor':min(pu,pd),'best':max(pu,pd),'gap':abs(pu-pd),
            'favoredSide':fav,'weakSide':weak,'favoredPayoff':float(payoff[fav]),'weakPayoff':float(payoff[weak]),
            'upQty':u,'downQty':d,'cost':c,'targetRepairDebt':float(self._aggregate_for_repair_side(weak)),
            'totalRepairDebt':float(self._aggregate_for_repair_side('UP')+self._aggregate_for_repair_side('DOWN')),
            'liveSlots':len(self.slot_key),'fills':int(getattr(self,'fills',0)),'submits':int(self.submits),
            'alternations':int(len([1 for i in range(1,len(self.fill_side_sequence)) if self.fill_side_sequence[i]['side']!=self.fill_side_sequence[i-1]['side']])),
            'qLadderRoute':None if self.q_ladder is None else self.q_ladder.get('route'),'pendingActive':self.q_pending_active is not None,
        }

    def _snap_prefix(self,t,qv):
        live={}
        live_keys=set(self.slot_key.values())
        for k,o in self.orders.items():
            if k in live_keys or base.v2.base.live(str(o.get('status') or '')):
                live[k]={z:o.get(z) for z in ('n','side','price','qty','cum','placed','status','cancelRequested')}
        x={
            't':int(t),'n':int(self.n),'inv':dict(self.inv),'cost':float(self.cost),'slotKey':dict(self.slot_key),'keyRole':dict(self.key_role),
            'liveOrders':live,'qLadder':copy.deepcopy(self.q_ladder),'qPendingActive':copy.deepcopy(self.q_pending_active),
            'responsibilities':self.serializable_lots(),'paymentRows':copy.deepcopy(self.resp_payment_rows),
            'placeHist':list(self.placeHist),'fillSequence':copy.deepcopy(self.fill_side_sequence),'quotes':norm(qv),
        }
        self.prefix=norm(x);self.prefixDigest=digest(self.prefix);self.prefixState=self._payoff_state(t)

    def _repair_role(self,side):
        return 'ECONOMIC_CORE' if self._core_for_side(side) is None else 'SATELLITE_REPAIR'

    def _forced_arm(self,t,qv,side,role):
        if role not in REPAIR_ROLES or self.q_ladder is not None or self.q_pending_active is not None:return None
        lot=self._oldest_for_repair_side(side)
        if lot is None:return None
        agg=float(self._aggregate_for_repair_side(side))
        if agg<=EPS:return None
        return {'t':int(t),'side':side,'role':role,'responsibilityId':int(lot['id']),
                'oldestRemainingQty':float(lot['remainingQty']),'aggregateOutstandingQty':agg,
                'bornAt':int(lot['bornAt']),'expandSide':lot['side'],'expandPrice':float(lot['price']),'qv':qv}

    def _force_one(self,t,qv,kind):
        if self.q_pending_active is not None:
            return {'ok':False,'reason':'PENDING_ACTIVE_PRESENT'}
        if len(self.slot_key)>=self.max_slots:return {'ok':False,'reason':'NO_FREE_SLOT'}
        side=str(self.spec['weakSide'] if kind=='NEXT_REPAIR' else self.spec['expandSide'])
        role=self._repair_role(side) if kind=='NEXT_REPAIR' else 'SATELLITE_EXPAND'
        self.q_arm=self._forced_arm(t,qv,side,role) if kind=='NEXT_REPAIR' else None
        try:
            cand=self._candidate_from_levels(side,True,False)
            if cand is None:return {'ok':False,'reason':'NO_CURRENT_CANDIDATE','side':side,'role':role}
            p,q,proj=cand;before_n=int(self.n);before_sub=int(self.submits)
            ok=bool(self._submit_role(int(t),side,role,float(p),float(q),proj,'MANAGEMENT_MAINLINE_ROLE_SWITCH_EXACT_FORK_V1'))
            key=f'{side}_{before_n}' if ok and int(self.submits)>before_sub else None
            self.branchKey=key;self.branchRole=role
            return {'ok':ok,'reason':None if ok else 'SUBMIT_FALSE','side':side,'role':role,'price':float(p),'qty':float(q),'key':key,
                    'qArmUsed':self.q_arm is not None}
        finally:self.q_arm=None

    def process(self,t):
        before=len(self.fill_accounting)
        super().process(t)
        if self.branchKey and self.branchResolution is None:
            new=self.fill_accounting[before:]
            hits=[a for a in new if str(a.get('key'))==str(self.branchKey)]
            if hits:
                self.branchResolution={'kind':'FILL','t':int(t),'fills':norm(hits),'state':self._payoff_state(t)}

    def _refresh_slots(self,t):
        before=len(self.slot_history)
        super()._refresh_slots(t)
        if self.branchKey and self.branchResolution is None:
            rel=[x for x in self.slot_history[before:] if x.get('event')=='SLOT_RELEASE' and str(x.get('key'))==str(self.branchKey)]
            if rel:self.branchResolution={'kind':'SLOT_RELEASE','t':int(t),'events':norm(rel),'state':self._payoff_state(t)}

    def _open_one_option(self,t,qv,end):
        if int(t)==int(self.spec['t']) and not self.seen:
            self.seen=True;self._snap_prefix(t,qv);before=len(self.slot_history);before_sub=int(self.submits);before_n=int(self.n)
            if self.branch=='NATIVE':
                out=super()._open_one_option(t,qv,end)
                new=self.slot_history[before:];subs=[x for x in new if x.get('event')=='ROLE_SLOT_SUBMIT' and int(x.get('t') or -1)==int(t)]
                newkeys=[]
                for n in range(before_n,int(self.n)):
                    for side in ('UP','DOWN'):
                        k=f'{side}_{n}'
                        if k in self.orders:newkeys.append(k)
                passive=[]
                for k in newkeys:
                    role=str(self.key_role.get(k,'UNASSIGNED'))
                    if k in getattr(self,'activeKeys',set()):continue
                    if role in REPAIR_ROLES|EXPAND_ROLES:passive.append(k)
                self.branchKey=passive[0] if len(passive)==1 else None
                self.branchRole=self.key_role.get(self.branchKey) if self.branchKey else None
                self.intervention={'branch':'NATIVE','newSlotEvents':norm(new),'newKeys':newkeys,'passiveKeys':passive,
                                   'nativeClass':self.spec.get('nativeClass'),'expectedNativeKey':self.spec.get('nativeKey')}
                self.postSubmitState=self._payoff_state(t);return out
            forced=self._force_one(t,qv,self.branch)
            self.intervention={'branch':self.branch,'forced':forced,'newSlotEvents':norm(self.slot_history[before:]),
                               'expectedScanRepairCandidate':norm(self.spec.get('repairCandidate')),
                               'expectedScanExpandCandidate':norm(self.spec.get('expandCandidate'))}
            self.postSubmitState=self._payoff_state(t);return
        return super()._open_one_option(t,qv,end)

    def run_branch(self):
        r=super().run_qty('__UNSCORED__')
        return {'raw':r,'branch':self.branch,'seen':self.seen,'prefixDigest':self.prefixDigest,'prefix':self.prefix,
                'prefixState':self.prefixState,'postSubmitState':self.postSubmitState,'intervention':self.intervention,
                'branchKey':self.branchKey,'branchRole':self.branchRole,'branchResolution':self.branchResolution}


def terminal(r,spec):
    x=r['raw'];u=float(x['upQty']);d=float(x['downQty']);c=float(x['buyNotional']);pay={'UP':u-c,'DOWN':d-c};fav=str(spec['expandSide']);weak=str(spec['weakSide'])
    return {'floor':float(x['floor']),'best':float(x['best']),'gap':float(x['best'])-float(x['floor']),
            'favoredPayoff':float(pay[fav]),'weakPayoff':float(pay[weak]),'upQty':u,'downQty':d,'buyNotional':c,
            'fills':int(x['fillEvents']),'submits':int(x['submits']),'alternations':int(x.get('fillSideAlternations') or 0),
            'activeSubmits':int((x.get('quantityLadderCounters') or {}).get('managedActiveSubmits',0)),
            'managedRepairQty':float(x.get('quantityManagedRepairQtyDiagnostic') or 0.0),
            'managedOverflowQty':float(x.get('quantityManagedOverflowQtyDiagnostic') or 0.0),
            'ledgerViolations':(x.get('quantityLedgerSummary') or {}).get('invariantViolations') or {},
            'maxSlots':int(x.get('maxSimultaneousSlots') or 0)}

def delta(a,b):
    if a is None or b is None:return None
    ks=('floor','best','gap','favoredPayoff','weakPayoff','upQty','downQty','cost','targetRepairDebt','totalRepairDebt','liveSlots','fills','submits','alternations')
    return {k:float(b[k])-float(a[k]) for k in ks if k in a and k in b}

def term_delta(a,b):
    ks=('floor','best','gap','favoredPayoff','weakPayoff','upQty','downQty','buyNotional','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty')
    return {k:float(b[k])-float(a[k]) for k in ks}

def action_class(role):return 'REPAIR' if str(role) in REPAIR_ROLES else ('EXPAND' if str(role) in EXPAND_ROLES else 'OTHER')

def same_terminal(a,b):
    keys=('floor','best','gap','favoredPayoff','weakPayoff','upQty','downQty','buyNotional','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty')
    return all(close(a[k],b[k]) for k in keys)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--specs',required=True);ap.add_argument('--output',required=True);ap.add_argument('--max-states',type=int,default=3)
    a=ap.parse_args();src=json.loads(Path(a.specs).read_text(encoding='utf-8'));specs=list(src.get('states') or src.get('events') or [])[:int(a.max_states)]
    specs=[s for s in specs if not bool(s.get('qPendingActive'))]
    if not specs:raise RuntimeError('no eligible specs')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='mgmt_role_switch_fork_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for mid in sorted({int(s['marketId']) for s in specs}):z.extract(f'tapes/{mid}.json.xz',root)
        for i,s in enumerate(specs,1):
            mid=int(s['marketId']);tape=root/'tapes'/f'{mid}.json.xz';rr={}
            for b in BRANCHES:
                sim=RoleSwitchFork(tape,s,b)
                try:rr[b]=sim.run_branch()
                finally:sim.close()
            digs={b:rr[b]['prefixDigest'] for b in BRANCHES};tm={b:terminal(rr[b],s) for b in BRANCHES}
            native_class=str(s.get('nativeClass'));same_branch='NEXT_REPAIR' if native_class=='REPAIR' else 'NEXT_REEXPAND'
            forced={b:(rr[b].get('intervention') or {}).get('forced') for b in ('NEXT_REPAIR','NEXT_REEXPAND')}
            checks={
                'allTriggered':all(rr[b]['seen'] for b in BRANCHES),
                'prefixParity':len(set(digs.values()))==1 and None not in digs.values(),
                'noPendingActiveAtPrefix':all(not bool((rr[b]['prefixState'] or {}).get('pendingActive')) for b in BRANCHES),
                'repairSubmitExercised':bool((forced['NEXT_REPAIR'] or {}).get('ok')),
                'reexpandSubmitExercised':bool((forced['NEXT_REEXPAND'] or {}).get('ok')),
                'allLedgerClean':all(not tm[b]['ledgerViolations'] for b in BRANCHES),
                'allMax4':all(tm[b]['maxSlots']<=4 for b in BRANCHES),
                'nativeSameClassParity':same_terminal(tm['NATIVE'],tm[same_branch]),
                'nativeClassMatchesSpec':action_class(rr['NATIVE'].get('branchRole'))==native_class,
            }
            res={b:rr[b]['branchResolution'] for b in BRANCHES}
            local={b:None if res[b] is None else {'kind':res[b]['kind'],'t':res[b]['t'],'lagMs':int(res[b]['t'])-int(s['t']),
                    'state':res[b]['state'],'deltaFromPrefix':delta(rr[b]['prefixState'],res[b]['state'])} for b in BRANCHES}
            td={b:term_delta(tm['NATIVE'],tm[b]) for b in ('NEXT_REPAIR','NEXT_REEXPAND')}
            # Direct action contrast is REEXPAND minus REPAIR; no scalar reward.
            contrast={k:float(tm['NEXT_REEXPAND'][k])-float(tm['NEXT_REPAIR'][k]) for k in ('floor','best','gap','favoredPayoff','weakPayoff','upQty','downQty','buyNotional','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty')}
            row={'marketId':mid,'t':int(s['t']),'stateSpec':s,'prefixDigests':digs,'interventions':{b:rr[b]['intervention'] for b in BRANCHES},
                 'checks':checks,'branchResolution':local,'terminalMetrics':tm,'terminalDeltaVsNative':td,
                 'reexpandMinusRepairTerminalVector':contrast,'valid':all(checks.values())}
            rows.append(row);print(json.dumps({'progress':i,'of':len(specs),'marketId':mid,'t':s['t'],'nativeClass':native_class,'valid':row['valid'],'checks':checks,'contrast':contrast},ensure_ascii=False),flush=True)
    out={'version':'MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_EXACT_FORK_V1_20260907','researchOnly':True,'runtimeAuthority':False,'stateCount':len(rows),
         'allCorrectnessPass':all(r['valid'] for r in rows),'rows':rows,
         'boundary':['current V3B exact-HFT/exact FIFO','same strict-past prefix all branches','one target receipt only','NATIVE unchanged current V3B','NEXT_REPAIR allocates exactly one available Passive action to current weak-side Repair using current V3B candidate/managed-arm semantics','NEXT_REEXPAND allocates exactly one available Passive action to current pre-branch dominant side using current V3B Pair candidate','existing live orders/responsibilities remain untouched','no extra slot/risk/credit/objective/Active authority','suffix immediately returns to current V3B','first branch fill or slot release is primary structural resolution; terminal vector secondary','no scalar reward/no winner/Target/future input','pending Active states excluded','max4/no dream fill/no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allCorrectnessPass':out['allCorrectnessPass'],'stateCount':len(rows)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
