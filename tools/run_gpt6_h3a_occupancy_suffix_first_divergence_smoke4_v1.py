"""H3a telemetry-only rerun of the frozen H4 smoke4 branches.

Tests whether submitted/live option occupancy changes the later controller suffix even
when realized direct intervention fill/payoff is identical. No policy authority change.
"""
from __future__ import annotations
import argparse, copy, importlib.util, json, math, os, tempfile, zipfile
from pathlib import Path
from itertools import combinations

HERE = Path(__file__).resolve().parent
H4_PATH = HERE / 'run_gpt6_h4_joint_candidate_factorial_fork_v1.py'
spec = importlib.util.spec_from_file_location('h3a_h4base', H4_PATH)
h4 = importlib.util.module_from_spec(spec); spec.loader.exec_module(h4)
EPS = h4.EPS
BRANCHES = h4.BRANCHES


def r10(x):
    return round(float(x or 0.0), 10)


def stable(x):
    if isinstance(x, dict): return {str(k): stable(v) for k,v in sorted(x.items(), key=lambda kv:str(kv[0]))}
    if isinstance(x, (list,tuple)): return [stable(v) for v in x]
    if isinstance(x, float): return round(x, 10)
    if isinstance(x, (str,int,bool)) or x is None: return x
    try: return round(float(x), 10)
    except Exception: return str(x)


class TraceFork(h4.JointCandidateFork):
    def __init__(self, tape, spec, branch):
        super().__init__(tape, spec, branch)
        self.h3trace=[]
        self.initialPostSubmitSnapshot=None

    def _snapshot(self, t):
        live=[]
        for sid,key in sorted(self.slot_key.items(), key=lambda kv:int(kv[0])):
            o=self.orders.get(key,{})
            qty=float(o.get('qty') or 0.0); cum=float(o.get('cum') or 0.0)
            live.append({
                'side':str(o.get('side') or ''), 'role':str(self.key_role.get(key,'UNASSIGNED')),
                'price':r10(o.get('price')), 'qty':r10(qty), 'remaining':r10(max(0.0,qty-cum)),
                'status':str(o.get('status') or ''), 'cancelRequested':bool(o.get('cancelRequested')),
                'active':bool(key in getattr(self,'activeKeys',set())),
            })
        u=float(self.inv['UP']); d=float(self.inv['DOWN']); c=float(self.cost)
        return stable({
            't':int(t),'liveSlots':len(self.slot_key),'liveOrders':live,
            'qLadderRoute':None if self.q_ladder is None else self.q_ladder.get('route'),
            'pendingActive':self.q_pending_active is not None,
            'repairDebtUP':float(self._aggregate_for_repair_side('UP')),
            'repairDebtDOWN':float(self._aggregate_for_repair_side('DOWN')),
            'responsibilityCount':len(self.serializable_lots()),
            'upQty':u,'downQty':d,'cost':c,'floor':min(u-c,d-c),'best':max(u-c,d-c),'gap':abs(u-d),
        })

    def _emit(self, kind, t, details):
        if not self.globalReady: return
        self.h3trace.append({'t':int(t),'lagMs':int(t)-int(self.spec['t']),'kind':kind,'details':stable(details),'state':self._snapshot(t)})

    def _open_one_option(self,t,qv,end):
        target = int(t)==int(self.spec['t']) and not self.seen
        out=super()._open_one_option(t,qv,end)
        if target and self.globalReady and self.initialPostSubmitSnapshot is None:
            self.initialPostSubmitSnapshot=self._snapshot(t)
        return out

    def submit(self,*args,**kwargs):
        before_n=int(self.n); before_sub=int(self.submits)
        out=super().submit(*args,**kwargs)
        if self.globalReady and int(self.submits)>before_sub:
            t=int(args[0] if args else kwargs.get('t'))
            news=[]
            for n in range(before_n,int(self.n)):
                for side in ('UP','DOWN'):
                    key=f'{side}_{n}'
                    if key not in self.orders: continue
                    o=self.orders[key]
                    news.append({'side':str(o.get('side') or side),'role':str(self.key_role.get(key,'UNASSIGNED')),
                                 'price':r10(o.get('price')),'qty':r10(o.get('qty')),
                                 'active':bool(key in getattr(self,'activeKeys',set()))})
            self._emit('NEW_SUBMIT',t,news)
        return out

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(sid); o=copy.deepcopy(self.orders.get(key,{})) if key else {}
        ok=super()._request_cancel(t,sid,reason)
        if ok and self.globalReady:
            self._emit('CANCEL_REQUEST',t,{'side':str(o.get('side') or ''),'role':str(self.key_role.get(key,'UNASSIGNED')),
                                           'price':r10(o.get('price')),'qty':r10(o.get('qty')),'reason':str(reason)})
        return ok

    def process(self,t):
        before=len(self.fill_accounting)
        out=super().process(t)
        if self.globalReady:
            for x in self.fill_accounting[before:]:
                key=str(x.get('key') or '')
                self._emit('FILL',t,{
                    'side':str(x.get('side') or ''),'role':str(self.key_role.get(key,'UNASSIGNED')),
                    'executionPrice':r10(x.get('executionPriceFromInheritedSubstrate')),
                    'confirmedQty':r10(x.get('confirmedQty')),'matchedRepairQty':r10(x.get('matchedRepairQty')),
                    'overflowQty':r10(x.get('overflowQty')),'isIntervention':bool(key in set(self.interventionKeys)),
                    'wasPrefixExisting':bool(key in set(self.prefixLiveKeys)),
                })
        return out

    def _refresh_slots(self,t):
        before=len(self.slot_history)
        out=super()._refresh_slots(t)
        if self.globalReady:
            for x in self.slot_history[before:]:
                if x.get('event')!='SLOT_RELEASE': continue
                key=str(x.get('key') or ''); o=self.orders.get(key,{})
                self._emit('SLOT_RELEASE',t,{'side':str(o.get('side') or ''),'role':str(self.key_role.get(key,'UNASSIGNED')),
                                             'price':r10(o.get('price')),'qty':r10(o.get('qty')),
                                             'status':str(o.get('status') or ''),'cancelRequested':bool(o.get('cancelRequested'))})
        return out


def direct_signature(lineage):
    z=lineage['categories']['INTERVENTION']
    return {
        'fills':int(z['fills']),'qty':r10(z['qty']),'repairQty':r10(z['repairQty']),'overflowQty':r10(z['overflowQty']),
        'upPayoffDelta':r10(z['upPayoffDelta']),'downPayoffDelta':r10(z['downPayoffDelta']),'notional':r10(z['notional'])
    }


def event_sem(e):
    return stable({'kind':e['kind'],'details':e['details']})


def grouped(trace):
    g={}
    for e in trace: g.setdefault(int(e['t']),[]).append(event_sem(e))
    for t in g: g[t]=sorted(g[t],key=lambda x:json.dumps(x,sort_keys=True,separators=(',',':')))
    return g


def state_at(trace, initial, t):
    s=initial
    for e in trace:
        if int(e['t'])<=int(t): s=e['state']
        else: break
    return s


def first_divergence(a,b):
    ga,gb=grouped(a['trace']),grouped(b['trace'])
    for t in sorted(set(ga)|set(gb)):
        if ga.get(t,[])!=gb.get(t,[]):
            return {'t':t,'lagMs':t-int(a['prefixT']),'leftEvents':ga.get(t,[]),'rightEvents':gb.get(t,[]),
                    'leftState':state_at(a['trace'],a['initialPostSubmitSnapshot'],t),
                    'rightState':state_at(b['trace'],b['initialPostSubmitSnapshot'],t)}
    return None


def terminal_equal(a,b):
    ks=('floor','best','gap','upQty','downQty','buyNotional','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty')
    return all(math.isclose(float(a[k]),float(b[k]),rel_tol=1e-10,abs_tol=1e-8) for k in ks)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--specs',required=True); ap.add_argument('--reference-result',required=True); ap.add_argument('--output',required=True)
    a=ap.parse_args(); specs=list(json.loads(Path(a.specs).read_text(encoding='utf-8'))['states']); ref=json.loads(Path(a.reference_result).read_text(encoding='utf-8'))
    ref_by_mid={int(r['marketId']):r for r in ref['rows']}; outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    rows=[]
    with tempfile.TemporaryDirectory(prefix='gpt6_h3a_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for s in specs: z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(specs,1):
            mid=int(s['marketId']); tape=root/'tapes'/f'{mid}.json.xz'; branches={}
            for bn in BRANCHES:
                sim=TraceFork(tape,s,bn)
                try:
                    raw=sim.run_branch(); term=h4.f.terminal(raw,s); lin=sim.lineage(term)
                    branches[bn]={'prefixT':int(s['t']),'prefixDigest':raw['prefixDigest'],'initialPostSubmitSnapshot':sim.initialPostSubmitSnapshot,
                                  'directSignature':direct_signature(lin),'trace':sim.h3trace,'terminal':term,'lineage':lin,
                                  'ledgerClean':not bool(term['ledgerViolations']),'max4':int(term['maxSlots'])<=4,'triggered':bool(raw['seen'])}
                finally: sim.close()
            # Instrumentation must be behavior-inert relative to frozen H4 result.
            parity={}
            for bn,b in branches.items(): parity[bn]=terminal_equal(b['terminal'],ref_by_mid[mid]['branches'][bn]['terminal'])
            groups={}
            for bn,b in branches.items():
                sk=json.dumps(b['directSignature'],sort_keys=True,separators=(',',':'))
                groups.setdefault(sk,[]).append(bn)
            pairs=[]
            for sk,names in groups.items():
                if len(names)<2: continue
                for left,right in combinations(names,2):
                    L,R=branches[left],branches[right]; fd=first_divergence(L,R)
                    sameTerm=terminal_equal(L['terminal'],R['terminal'])
                    occDifferent=L['initialPostSubmitSnapshot']['liveOrders']!=R['initialPostSubmitSnapshot']['liveOrders']
                    pairs.append({'left':left,'right':right,'directSignature':L['directSignature'],'zeroDirectFill':L['directSignature']['fills']==0,
                                  'initialOccupancyDifferent':occDifferent,'terminalSame':sameTerm,'firstDivergence':fd,
                                  'leftTerminal':L['terminal'],'rightTerminal':R['terminal']})
            material=[p for p in pairs if p['initialOccupancyDifferent'] and not p['terminalSame']]
            zeroMaterial=[p for p in material if p['zeroDirectFill']]
            checks={'prefixParity':len({b['prefixDigest'] for b in branches.values()})==1,
                    'allLedgerClean':all(b['ledgerClean'] for b in branches.values()),'allMax4':all(b['max4'] for b in branches.values()),
                    'allTriggered':all(b['triggered'] for b in branches.values()),'referenceTerminalParityAllBranches':all(parity.values())}
            row={'marketId':mid,'t':int(s['t']),'checks':checks,'correctnessPass':all(checks.values()),'referenceTerminalParity':parity,
                 'branchSummary':{bn:{'directSignature':b['directSignature'],'initialPostSubmitSnapshot':b['initialPostSubmitSnapshot'],'terminal':b['terminal'],'traceEvents':len(b['trace'])} for bn,b in branches.items()},
                 'sameDirectFillPairs':pairs,'materialSameFillPairs':len(material),'materialZeroFillPairs':len(zeroMaterial)}
            rows.append(row)
            (outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(specs),'marketId':mid,'correct':row['correctnessPass'],'sameFillPairs':len(pairs),'material':len(material),'zeroMaterial':len(zeroMaterial)},ensure_ascii=False),flush=True)
    allpairs=[p for r in rows for p in r['sameDirectFillPairs']]; material=[p for p in allpairs if p['initialOccupancyDifferent'] and not p['terminalSame']]; zero=[p for p in material if p['zeroDirectFill']]
    markets_material=sorted({r['marketId'] for r in rows if r['materialSameFillPairs']>0}); markets_zero=sorted({r['marketId'] for r in rows if r['materialZeroFillPairs']>0})
    supported=bool(zero) and len(markets_zero)>=2
    payload={'version':'GPT6_H3A_OCCUPANCY_SUFFIX_FIRST_DIVERGENCE_SMOKE4_V1_20260907','researchOnly':True,'runtimeAuthority':False,
             'allCorrectnessPass':all(r['correctnessPass'] for r in rows),'rows':rows,
             'summary':{'sameDirectFillPairs':len(allpairs),'materialSameFillPairs':len(material),'materialZeroFillPairs':len(zero),
                        'marketsWithMaterialSameFill':markets_material,'marketsWithMaterialZeroFill':markets_zero,
                        'h3aDiagnosticSupport':supported},
             'decision':'SUPPORT_H3A_OCCUPANCY_CONTINUATION' if supported else 'H3A_NOT_YET_SUPPORTED',
             'boundary':['telemetry-only subclass; terminal parity to H4 is required','same frozen H4 prefix/candidates','same realized direct intervention signature within pair','no fixed-time policy rule','realistic HFT/no dream fill','consumed H100/no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'decision':payload['decision'],'summary':payload['summary'],'allCorrectnessPass':payload['allCorrectnessPass']},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
