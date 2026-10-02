from __future__ import annotations
import argparse, copy, hashlib, json, math, os, tempfile, zipfile
from pathlib import Path
try:
    from tools import run_management_mainline_v3b_role_switch_exact_fork_v1 as rs
except ImportError:
    import importlib.util
    _p=Path(__file__).with_name('run_management_mainline_v3b_role_switch_exact_fork_v1.py')
    _sp=importlib.util.spec_from_file_location('run_management_mainline_v3b_role_switch_exact_fork_v1',_p); rs=importlib.util.module_from_spec(_sp); _sp.loader.exec_module(rs)
from tools import run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS
REPAIR_ROLES=rs.REPAIR_ROLES
EXPAND_ROLES=rs.EXPAND_ROLES
RESPONSES=('NATIVE_RESPONSE','HOLD_ONE_RESPONSE','ALT_ROLE_RESPONSE')

def norm(x): return rs.norm(x)
def digest(x): return rs.digest(x)
def close(a,b,tol=1e-8): return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=tol)

def terminal(raw,spec): return rs.terminal({'raw':raw},spec)

def termdiff(a,b): return rs.term_delta(a,b)

def state_delta(a,b): return rs.delta(a,b)

def action_class(role): return rs.action_class(role)

def physical_core(t):
    ks=('floor','best','gap','favoredPayoff','weakPayoff','upQty','downQty','buyNotional','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty')
    return {k:t[k] for k in ks}

def eq_core(a,b): return all(close(a[k],b[k]) for k in a)

class H3ResponseFork(rs.RoleSwitchFork):
    def __init__(self,tape,spec,initial_kind,response_mode='PROBE',response_t=None,response_native_class=None):
        super().__init__(tape,spec,initial_kind)
        self.initialKind=initial_kind
        self.responseMode=response_mode
        self.responseT=None if response_t is None else int(response_t)
        self.responseNativeClass=response_native_class
        self.initialFillT=None; self.initialFillState=None; self.initialFillRows=[]
        self.responseSeen=False; self.responsePrefix=None; self.responsePrefixDigest=None; self.responsePrefixState=None
        self.responseKeys=[]; self.responseAction=None; self.responseIntervention=None
        self.initialPrefixLiveKeys=set(); self.initialKey=None
        self.responseFillRows=[]

    def _response_snap(self,t,qv):
        live={}; live_keys=set(self.slot_key.values())
        for k,o in self.orders.items():
            if k in live_keys or v3b.base.v2.base.live(str(o.get('status') or '')):
                live[k]={z:o.get(z) for z in ('n','side','price','qty','cum','placed','status','cancelRequested')}
        x={'t':int(t),'n':int(self.n),'inv':dict(self.inv),'cost':float(self.cost),'slotKey':dict(self.slot_key),'keyRole':dict(self.key_role),
           'liveOrders':live,'qLadder':copy.deepcopy(self.q_ladder),'qPendingActive':copy.deepcopy(self.q_pending_active),
           'responsibilities':self.serializable_lots(),'paymentRows':copy.deepcopy(self.resp_payment_rows),'placeHist':list(self.placeHist),
           'fillSequence':copy.deepcopy(self.fill_side_sequence),'quotes':norm(qv)}
        self.responsePrefix=norm(x); self.responsePrefixDigest=digest(self.responsePrefix); self.responsePrefixState=self._payoff_state(t)

    def _current_sides(self):
        u=float(self.inv['UP']); d=float(self.inv['DOWN'])
        if abs(u-d)<=EPS:
            weak=str(self.spec['weakSide']); expand=str(self.spec['expandSide'])
        elif u<d:
            weak='UP'; expand='DOWN'
        else:
            weak='DOWN'; expand='UP'
        return weak,expand

    def _force_dynamic(self,t,qv,kind):
        if self.q_pending_active is not None:return {'ok':False,'reason':'PENDING_ACTIVE_PRESENT'}
        if len(self.slot_key)>=self.max_slots:return {'ok':False,'reason':'NO_FREE_SLOT'}
        weak,expand=self._current_sides(); side=weak if kind=='NEXT_REPAIR' else expand
        role=self._repair_role(side) if kind=='NEXT_REPAIR' else 'SATELLITE_EXPAND'
        self.q_arm=self._forced_arm(t,qv,side,role) if kind=='NEXT_REPAIR' else None
        try:
            cand=self._candidate_from_levels(side,True,False)
            if cand is None:return {'ok':False,'reason':'NO_CURRENT_CANDIDATE','side':side,'role':role}
            p,q,proj=cand; before_n=int(self.n); before_sub=int(self.submits)
            ok=bool(self._submit_role(int(t),side,role,float(p),float(q),proj,'GPT6_H3_POST_INTERVENTION_RESPONSE_V1'))
            key=f'{side}_{before_n}' if ok and int(self.submits)>before_sub else None
            if key:self.responseKeys.append(key)
            return {'ok':ok,'reason':None if ok else 'SUBMIT_FALSE','side':side,'role':role,'price':float(p),'qty':float(q),'key':key,'kind':kind}
        finally:self.q_arm=None

    def process(self,t):
        before=len(self.fill_accounting)
        super().process(t)
        new=list(self.fill_accounting[before:])
        if self.branchKey and self.initialFillT is None:
            hits=[x for x in new if str(x.get('key'))==str(self.branchKey) and float(x.get('confirmedQty') or 0)>EPS]
            if hits:
                self.initialFillT=int(t); self.initialFillRows=norm(hits); self.initialFillState=self._payoff_state(t); self.initialKey=str(self.branchKey)
        if self.responseKeys:
            hits=[x for x in new if str(x.get('key')) in set(self.responseKeys) and float(x.get('confirmedQty') or 0)>EPS]
            if hits:self.responseFillRows.extend(norm(hits))

    def _new_passive_keys(self,before_n):
        keys=[]
        for n in range(int(before_n),int(self.n)):
            for side in ('UP','DOWN'):
                k=f'{side}_{n}'
                if k not in self.orders:continue
                if k in getattr(self,'activeKeys',set()):continue
                role=str(self.key_role.get(k,'UNASSIGNED'))
                if role in REPAIR_ROLES|EXPAND_ROLES:keys.append(k)
        return keys

    def _record_native_response(self,t,keys):
        acts=[]
        for k in keys:
            o=self.orders[k]; role=str(self.key_role.get(k,'UNASSIGNED'))
            acts.append({'key':k,'side':str(o.get('side')),'role':role,'class':action_class(role),'price':float(o.get('price') or 0),'qty':float(o.get('qty') or 0)})
        self.responseKeys=list(keys); self.responseAction=acts
        if acts and self.responseNativeClass is None:self.responseNativeClass=str(acts[0]['class'])

    def _open_one_option(self,t,qv,end):
        # Initial intervention at the original exact seam is inherited unchanged.
        if int(t)==int(self.spec['t']) and not self.seen:
            out=super()._open_one_option(t,qv,end)
            self.initialPrefixLiveKeys=set((self.prefix or {}).get('liveOrders',{}))
            return out

        # PROBE: after confirmed intervention fill, find first later native open call that actually submits a passive Repair/Expand carrier.
        if self.responseMode=='PROBE' and self.initialFillT is not None and int(t)>int(self.initialFillT) and not self.responseSeen:
            before_n=int(self.n); before_sub=int(self.submits)
            out=super()._open_one_option(t,qv,end)
            keys=self._new_passive_keys(before_n)
            if keys and int(self.submits)>before_sub:
                self.responseSeen=True; self.responseT=int(t); self._response_snap(t,qv); self._record_native_response(t,keys)
                self.responseIntervention={'mode':'PROBE_NATIVE','keys':list(keys),'actions':copy.deepcopy(self.responseAction)}
            return out

        # Fixed second-stage exact receipt.
        if self.responseMode!='PROBE' and self.responseT is not None and int(t)==int(self.responseT) and not self.responseSeen:
            self.responseSeen=True; self._response_snap(t,qv); before_n=int(self.n); before_sub=int(self.submits)
            if self.responseMode=='NATIVE_RESPONSE':
                out=super()._open_one_option(t,qv,end); keys=self._new_passive_keys(before_n); self._record_native_response(t,keys)
                self.responseIntervention={'mode':self.responseMode,'keys':list(keys),'actions':copy.deepcopy(self.responseAction)}; return out
            if self.responseMode=='HOLD_ONE_RESPONSE':
                self.responseIntervention={'mode':self.responseMode,'suppressedExactReceipt':int(t),'keys':[]}; return None
            if self.responseMode=='ALT_ROLE_RESPONSE':
                alt='NEXT_REEXPAND' if str(self.responseNativeClass)=='REPAIR' else 'NEXT_REPAIR'
                forced=self._force_dynamic(t,qv,alt)
                self.responseIntervention={'mode':self.responseMode,'nativeClass':self.responseNativeClass,'altKind':alt,'forced':copy.deepcopy(forced),'keys':list(self.responseKeys)}
                return None
            raise ValueError(self.responseMode)
        return super()._open_one_option(t,qv,end)

    def lineage(self,term):
        cats={k:{'fills':0,'qty':0.0,'upPayoffDelta':0.0,'downPayoffDelta':0.0,'notional':0.0,'keys':set()} for k in ('INITIAL_INTERVENTION','RESPONSE','PREFIX_EXISTING','OTHER_SUFFIX')}
        ik={str(self.initialKey)} if self.initialKey else set(); rk=set(map(str,self.responseKeys)); pk=set(map(str,self.initialPrefixLiveKeys))
        start=0
        for x in list(getattr(self,'fill_accounting',[]) or [])[start:]:
            key=str(x.get('key')); q=float(x.get('confirmedQty') or 0); p=float(x.get('executionPriceFromInheritedSubstrate') or 0); side=str(x.get('side'))
            if q<=EPS:continue
            if key in ik:cat='INITIAL_INTERVENTION'
            elif key in rk:cat='RESPONSE'
            elif key in pk:cat='PREFIX_EXISTING'
            else:cat='OTHER_SUFFIX'
            du=q*(1-p) if side=='UP' else -q*p; dd=q*(1-p) if side=='DOWN' else -q*p
            z=cats[cat]; z['fills']+=1; z['qty']+=q; z['upPayoffDelta']+=du; z['downPayoffDelta']+=dd; z['notional']+=q*p; z['keys'].add(key)
        for z in cats.values():z['keys']=sorted(z['keys'])
        return cats

    def run_h3(self):
        raw=super().run_qty('__UNSCORED__')
        return {'raw':raw,'seen':self.seen,'prefixDigest':self.prefixDigest,'prefixState':self.prefixState,'postSubmitState':self.postSubmitState,
                'initialKey':self.initialKey,'initialFillT':self.initialFillT,'initialFillState':self.initialFillState,'initialFillRows':self.initialFillRows,
                'responseSeen':self.responseSeen,'responseT':self.responseT,'responsePrefixDigest':self.responsePrefixDigest,'responsePrefixState':self.responsePrefixState,
                'responseNativeClass':self.responseNativeClass,'responseAction':self.responseAction,'responseIntervention':self.responseIntervention,'responseFillRows':self.responseFillRows}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--base-specs',required=True); ap.add_argument('--prereg',required=True); ap.add_argument('--output',required=True)
    a=ap.parse_args(); base=json.loads(Path(a.base_specs).read_text(encoding='utf-8')); by={(int(s['marketId']),int(s['t'])):s for s in base['states']}
    pre=json.loads(Path(a.prereg).read_text(encoding='utf-8')); specs=[]
    for x in pre['states']:
        s=copy.deepcopy(by[(int(x['marketId']),int(x['t']))]); s['initialKind']=str(x['initialKind']); specs.append(s)
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.')); rows=[]
    with tempfile.TemporaryDirectory(prefix='gpt6_h3_') as tmpdir:
        root=Path(tmpdir)
        with zipfile.ZipFile(a.bundle) as z:
            for s in specs:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(specs,1):
            tape=root/'tapes'/f"{int(s['marketId'])}.json.xz"; initial=str(s['initialKind'])
            probe=H3ResponseFork(tape,s,initial,'PROBE')
            try: pr=probe.run_h3(); pterm=terminal(pr['raw'],s); plin=probe.lineage(pterm)
            finally: probe.close()
            exercised=bool(pr['initialFillT'] is not None and pr['responseSeen'] and pr['responseT'] is not None and pr['responseNativeClass'] in ('REPAIR','EXPAND'))
            branches={}
            if exercised:
                for mode in RESPONSES:
                    sim=H3ResponseFork(tape,s,initial,mode,pr['responseT'],pr['responseNativeClass'])
                    try:
                        rr=sim.run_h3(); tt=terminal(rr['raw'],s); lin=sim.lineage(tt)
                        branches[mode]={'rawMeta':{k:rr[k] for k in rr if k!='raw'},'terminal':tt,'lineage':lin,'ledgerClean':not bool(tt['ledgerViolations']),'max4':int(tt['maxSlots'])<=4}
                    finally: sim.close()
            checks={'initialPrefixParity':bool(exercised and len({branches[m]['rawMeta']['prefixDigest'] for m in RESPONSES})==1),
                    'initialConfirmedFill':bool(pr['initialFillT'] is not None),
                    'responseFound':bool(pr['responseSeen']),
                    'responsePrefixParity':bool(exercised and len({branches[m]['rawMeta']['responsePrefixDigest'] for m in RESPONSES})==1 and None not in {branches[m]['rawMeta']['responsePrefixDigest'] for m in RESPONSES}),
                    'nativeResponseClassParity':bool(exercised and branches['NATIVE_RESPONSE']['rawMeta']['responseNativeClass']==pr['responseNativeClass']),
                    'nativeReferenceTerminalParity':bool(exercised and eq_core(physical_core(branches['NATIVE_RESPONSE']['terminal']),physical_core(pterm))),
                    'allLedgerClean':bool(exercised and all(branches[m]['ledgerClean'] for m in RESPONSES)),
                    'allMax4':bool(exercised and all(branches[m]['max4'] for m in RESPONSES))}
            initial_delta=state_delta(pr['prefixState'],pr['initialFillState']) if pr['initialFillState'] is not None else None
            response_effects={}
            if exercised:
                nt=branches['NATIVE_RESPONSE']['terminal']
                for m in ('HOLD_ONE_RESPONSE','ALT_ROLE_RESPONSE'):
                    response_effects[m+'-NATIVE']=termdiff(nt,branches[m]['terminal'])
            row={'marketId':int(s['marketId']),'t':int(s['t']),'initialKind':initial,'mechanismExercised':exercised,'probe':{'meta':{k:pr[k] for k in pr if k!='raw'},'terminal':pterm,'lineage':plin},
                 'checks':checks,'correctnessPass':bool(exercised and all(checks.values())),'initialFillMinusPrefix':initial_delta,'branches':branches,'responseEffects':response_effects}
            rows.append(row); (outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(specs),'marketId':s['marketId'],'exercised':exercised,'initialFillT':pr['initialFillT'],'responseT':pr['responseT'],'nativeClass':pr['responseNativeClass'],'correct':row['correctnessPass']},ensure_ascii=False),flush=True)
    exercised_rows=[r for r in rows if r['mechanismExercised']]
    agg={'seams':len(rows),'exercised':len(exercised_rows),'correct':sum(r['correctnessPass'] for r in rows)}
    if exercised_rows:
        for mode in ('HOLD_ONE_RESPONSE-NATIVE','ALT_ROLE_RESPONSE-NATIVE'):
            vals=[r['responseEffects'][mode] for r in exercised_rows]
            agg[mode]={k:{'sum':sum(float(v[k]) for v in vals),'positive':sum(float(v[k])>1e-8 for v in vals),'negative':sum(float(v[k])<-1e-8 for v in vals),'zero':sum(abs(float(v[k]))<=1e-8 for v in vals)} for k in ('floor','best','favoredPayoff','weakPayoff','fills','submits','alternations','buyNotional')}
    payload={'version':'GPT6_H3_POST_INTERVENTION_RESPONSE_FORK_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'aggregate':agg,
             'boundary':['consumed exercised-witness mechanism smoke only','initial intervention is current V3B legal Repair/ReExpand at frozen exact seam','second seam is first actual native passive submit strictly after confirmed initial intervention fill','second-stage HOLD suppresses one exact receipt only','ALT uses opposite current economic role only if legal','suffix immediately returns to current V3B','no fixed seconds/window/dwell rule','realistic HFT/no dream fill','no NEW24','no 8781'],
             'limitations':['witnesses were selected using known intervention-fill outcome to exercise mechanism; no breadth/promotion inference allowed','ALT may be physically unavailable at some response prefixes','lineage is accounting attribution, not full mediation proof'],
             'sha256':{'runner':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'prereg':hashlib.sha256(Path(a.prereg).read_bytes()).hexdigest()}}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
