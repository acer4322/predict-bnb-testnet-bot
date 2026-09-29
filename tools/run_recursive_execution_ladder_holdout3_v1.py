"""Preregistered recursive execution-ladder holdout3.
A = frozen first-cycle immediate priority-loss + one-tick protected Active only.
B = after first Active fill and first post-Active Expand fill, next same-side Repair/Core intent gets one inside-spread Passive GTX + inherited TTL retention.
C = same B; if second Passive terminal-zero-fills, one same-intent Active relay with +1 venue tick limit protection.
No third cycle.
"""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
# Load existing second-cycle machinery.
_STAGED_REC=Path.cwd()/'.lan_worker_v1'/'staging'/'run_recursive_execution_ladder_second_cycle_1826386_v1.py'
if _STAGED_REC.exists():
    sp=importlib.util.spec_from_file_location('staged_recursive_core',_STAGED_REC);rec=importlib.util.module_from_spec(sp);sp.loader.exec_module(rec)
else:
    import tools.run_recursive_execution_ladder_second_cycle_1826386_v1 as rec
# Load one-tick protected Active implementation.
_STAGED_PP=Path.cwd()/'.lan_worker_v1'/'staging'/'run_active_one_tick_price_protection_replication6_v1.py'
if _STAGED_PP.exists():
    sp2=importlib.util.spec_from_file_location('staged_pp_recursive',_STAGED_PP);pp=importlib.util.module_from_spec(sp2);sp2.loader.exec_module(pp)
else:
    import tools.run_active_one_tick_price_protection_replication6_v1 as pp
rr=rec.rr;v1=rec.v1;EPS=1e-9;TICK=0.01;ACTIVE_WINDOW_MS=500
MIDS=(1827839,1829561,1830604)
CELLS=('A_FIRST_CYCLE_ONLY','B_SECOND_CYCLE_INSIDE_SPREAD_TTL','C_SECOND_CYCLE_FULL_LADDER')

class RecursiveProtectedSim(rec.RecursiveLadderSim):
    def __init__(self,tape,cell,trace_path):
        mapped={CELLS[0]:rec.CELLS[0],CELLS[1]:rec.CELLS[1],CELLS[2]:rec.CELLS[2]}[cell]
        super().__init__(tape,mapped,trace_path)
        self.outer_cell=cell;self.first_priority_event=None;self.first_priority_cancel=False;self.price_protection_ticks=1
    def _reanchor_stale(self,t):
        # First-cycle immediate priority-loss cancellation; explicit terminal still required before Active.
        if self.probe_key and not self.first_priority_cancel:
            o=self.orders.get(self.probe_key)
            if o and not o.get('cancelRequested') and float(o.get('cum') or 0.0)<=EPS:
                qv=rr.isp.base.v2.base.quotes(self.book)
                if qv:
                    side=str(o['side']);bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);px=float(o['price'])
                    if bid>px+EPS:
                        sid=next((sid for sid,key in self.slot_key.items() if key==self.probe_key),None)
                        if sid is not None:
                            self.first_priority_event={'t':int(t),'key':self.probe_key,'side':side,'role':self.key_role.get(self.probe_key),
                                'ownPrice':px,'bestBid':bid,'bestAsk':ask,'ageMs':int(t)-int(o['placed']),'cum':float(o.get('cum') or 0.0)}
                            ok=self._request_cancel(t,int(sid),'FIRST_CYCLE_PRIORITY_LOSS');self.first_priority_event['cancelRequestedSuccessfully']=bool(ok);self.first_priority_cancel=bool(ok)
        return super()._reanchor_stale(t)
    def _submit_active_relay(self,t,qv):
        # Reuse validated one-tick protected first-cycle Active implementation.
        return pp.ProtectedActiveSim._submit_active_relay(self,t,qv)
    def _submit_second_active(self,t,qv):
        src=self.second_pending_active
        if not src:return False
        side=src['side'];role=src['role'];qty=float(src['remainingQty']);bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);limit=round(min(0.99,ask+TICK),10)
        if qty<=EPS:self.second_block='ZERO_SECOND_REMAINING';return False
        if limit<=ask+EPS:self.second_block='SECOND_PROTECTION_NOT_AVAILABLE';return False
        if len(self.slot_key)>=self.max_slots:self.second_block='NO_SECOND_ACTIVE_SLOT';return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:self.second_block='NO_SECOND_ACTIVE_SLOT';return False
        n=self.n;self.n+=1;ex=rr.isp.base.v2.base.ex;ns,np=ex.native_order(side,limit)
        try:
            if ns=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),np,qty,ex.hbt.GTC,ex.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),np,qty,ex.hbt.GTC,ex.LIMIT,False))
        except Exception as exc:self.second_block='SECOND_ACTIVE_EXCEPTION:'+str(exc);return False
        key=f'{side}_{n}';self.orders[key]={'n':n,'side':side,'price':limit,'qty':qty,'cum':0.,'placed':int(t),'status':'NEW','cancelRequested':False}
        self.placeHist.append((int(t),side,qty,limit));self.submits+=1;self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,limit,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        self.detail_outcomes[key]={'decisionId':self.detail_index,'key':key,'role':role,'side':side,'route':'SECOND_CYCLE_ACTIVE_RELAY_PROTECTED_1T',
            'submittedAt':int(t),'submittedPrice':limit,'submittedQty':qty,'fills':[],'terminal':None}
        opp='DOWN' if side=='UP' else 'UP';oq=sum(float(a) for a,_ in self.un[opp]);avg=self.unmatched_avg(opp) if oq>EPS else None
        self.second_active_used=True;self.second_active_key=key;self.second_active_submitted_at=int(t)
        self.second_active_meta={'sourceKey':src['sourceKey'],'submittedAt':int(t),'side':side,'role':role,'decisionAsk':ask,'price':limit,'limitPrice':limit,
            'priceProtectionTicks':1,'qty':qty,'bidAtRelay':bid,'askAtRelay':ask,'oppositeUnmatchedQty':oq,'oppositeUnmatchedAverage':avg,
            'pairSum':(avg+limit) if avg is not None else None,'pairOverage':(avg+limit-1) if avg is not None else None,'submitRc':rc}
        self.slot_history.append({'t':int(t),'event':'SECOND_CYCLE_ACTIVE_RELAY_SUBMIT','sourceKey':src['sourceKey'],'key':key,'slotId':int(free),
            'side':side,'role':role,'decisionAsk':ask,'price':limit,'priceProtectionTicks':1,'qty':qty,'submitRc':rc})
        return True
    def run_recursive_protected(self):
        r=super().run_recursive();r.update({'outerCell':self.outer_cell,'firstPriorityLossEvent':self.first_priority_event,'firstPriorityCancelRequested':self.first_priority_cancel});return r

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(eq(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def trigger_sig(x):
    x=x or {};keys=('t','key','side','role','ownPrice','bestBid','bestAsk','ageMs','cum','cancelRequestedSuccessfully');return {k:x.get(k) for k in keys}
def source_sig(r):
    pc=r.get('probeCandidate') or {};return {k:pc.get(k) for k in ('side','role','probePrice','probeQty','probePairSum')}
def first_relay_sig(r):
    rm=r.get('relayMeta') or {};ro=r.get('relayOutcome') or {};fs=ro.get('fills') or []
    return {'submittedAt':rm.get('submittedAt'),'side':rm.get('side'),'role':rm.get('role'),'decisionAsk':rm.get('decisionAsk',rm.get('askAtRelay')),
        'limitPrice':rm.get('limitPrice',rm.get('price')),'qty':rm.get('qty'),'terminal':(ro.get('terminal') or {}).get('status'),
        'fillQty':sum(float(x.get('confirmedQty') or 0) for x in fs),'actualPrices':[x.get('executionPriceFromInheritedSubstrate') for x in fs]}
def second_summary(r):
    sm=r.get('secondSourceMeta') or {};so=r.get('secondSourceOutcome') or {};am=r.get('secondActiveMeta') or {};ao=r.get('secondActiveOutcome') or {}
    sf=so.get('fills') or [];af=ao.get('fills') or []
    return {'postExpandKey':r.get('postActiveExpandFillKey'),'postExpandFillAt':r.get('postActiveExpandFillAt'),'sourceUsed':r.get('secondSourceUsed'),
        'sourceSide':sm.get('side'),'sourceRole':sm.get('role'),'inheritedPrice':sm.get('inheritedPrice'),'passivePrice':sm.get('probePrice'),'passiveQty':sm.get('probeQty'),'passivePair':sm.get('pairSum'),
        'sourceTerminal':(so.get('terminal') or {}).get('status'),'sourceFillQty':sum(float(x.get('confirmedQty') or 0) for x in sf),'sourceRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in sf),
        'cancelSuppressed':r.get('secondSourceCancelSuppressed'),'activeUsed':r.get('secondActiveUsed'),'activeDecisionAsk':am.get('decisionAsk',am.get('askAtRelay')),
        'activeLimit':am.get('limitPrice',am.get('price')),'activePair':am.get('pairSum'),'activeTerminal':(ao.get('terminal') or {}).get('status'),
        'activeFillQty':sum(float(x.get('confirmedQty') or 0) for x in af),'activeRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in af),'activeActualPrices':[x.get('executionPriceFromInheritedSubstrate') for x in af],
        'block':r.get('secondBlock')}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    op=Path(a.output).resolve();
    if op.exists():ap.error('Do not overwrite')
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'));refs={int(r['marketId']):r for r in ref['rows']}
    if tuple(sorted(refs))!=tuple(sorted(MIDS)):raise RuntimeError('reference mids mismatch')
    if v1.sha256(a.bundle)!=ref['bundleSha256']:raise RuntimeError('bundle mismatch')
    fields=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','economicRepairQty','economicOverflowQty')
    trace_dir=op.parent/(op.stem+'_traces');trace_dir.mkdir(parents=True,exist_ok=False);rows=[];checks=[]
    with tempfile.TemporaryDirectory(prefix='recursive_holdout3_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in MIDS:z.extract(f'tapes/{mid}.json.xz',root)
        for mid in MIDS:
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper();rf=refs[mid];by={}
            for cell in CELLS:
                tp=trace_dir/f'{mid}_{cell}.jsonl';sim=RecursiveProtectedSim(tape,cell,tp)
                try:r=sim.run_recursive_protected()
                finally:sim.close()
                r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional'];row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,'decisionTracePath':str(tp),**r};rows.append(row);by[cell]=row;v1.write_json(trace_dir/f'{mid}_{cell}_result.json',row)
                if cell==CELLS[0]:
                    parity={f:eq(row[f],rf[f]) for f in fields};row['baselineParity']=parity
                    if not all(parity.values()):raise RuntimeError(f'A parity fail {mid}: {parity}')
                    if not eq(trigger_sig(row.get('firstPriorityLossEvent')),trigger_sig(rf.get('priorityLossEvent'))):raise RuntimeError(f'first trigger parity {mid}')
                    if not eq(source_sig(row),source_sig(rf)):raise RuntimeError(f'first source parity {mid}')
                    if not eq(first_relay_sig(row),first_relay_sig(rf)):raise RuntimeError(f'first relay parity {mid}')
                print(json.dumps({'marketId':mid,'cell':cell,'firstPriority':row.get('firstPriorityLossEvent'),'firstRelay':first_relay_sig(row),'second':second_summary(row),
                    'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'repairQty':row['economicRepairQty'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor']},allow_nan=False),flush=True)
            A,B,C=by[CELLS[0]],by[CELLS[1]],by[CELLS[2]]
            # first cycle and first post-Active Expand fill must remain identical across cells.
            first_contract={'firstPrioritySame':eq(trigger_sig(A.get('firstPriorityLossEvent')),trigger_sig(B.get('firstPriorityLossEvent'))) and eq(trigger_sig(A.get('firstPriorityLossEvent')),trigger_sig(C.get('firstPriorityLossEvent'))),
                'firstRelaySame':eq(first_relay_sig(A),first_relay_sig(B)) and eq(first_relay_sig(A),first_relay_sig(C)),
                'postExpandSame':A.get('postActiveExpandFillAt')==B.get('postActiveExpandFillAt')==C.get('postActiveExpandFillAt') and A.get('postActiveExpandFillKey')==B.get('postActiveExpandFillKey')==C.get('postActiveExpandFillKey')}
            if not all(first_contract.values()):raise RuntimeError(f'pre-second contract fail {mid}: {first_contract}')
            sb,sc=second_summary(B),second_summary(C)
            for k in ('sourceUsed','sourceSide','sourceRole','inheritedPrice','passivePrice','passiveQty'):
                if not eq(sb[k],sc[k]):raise RuntimeError(f'B/C second source mismatch {mid} {k}')
            if not sb['sourceUsed']:raise RuntimeError(f'second source not used {mid}')
            if sc['activeUsed']:
                if sc['sourceTerminal'] in (None,'FILLED') or sc['sourceFillQty']>EPS:raise RuntimeError(f'second Active invalid terminal {mid}')
                if sc['activeLimit'] is None or sc['activeDecisionAsk'] is None or abs(float(sc['activeLimit'])-float(sc['activeDecisionAsk'])-TICK)>1e-9:raise RuntimeError(f'second protection not one tick {mid}')
            checks.append({'marketId':mid,'firstContract':first_contract,'B':sb,'C':sc})
    summary={cell:{'markets':len([r for r in rows if r['cell']==cell]),'totalFills':sum(int(r['fillEvents']) for r in rows if r['cell']==cell),'totalAlternations':sum(int(r['fillSideAlternations']) for r in rows if r['cell']==cell),'totalRepairQty':sum(float(r['economicRepairQty']) for r in rows if r['cell']==cell),'secondSourceFills':sum(second_summary(r)['sourceFillQty']>EPS for r in rows if r['cell']==cell),'secondActiveFills':sum(second_summary(r)['activeFillQty']>EPS for r in rows if r['cell']==cell)} for cell in CELLS}
    v1.write_json(op,{'version':'RECURSIVE_EXECUTION_LADDER_HOLDOUT3_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':list(MIDS),'rows':rows,'checks':checks,'summary':summary,
        'bundleSha256':v1.sha256(a.bundle),'referenceSha256':v1.sha256(a.reference),'automaticScaleAllowed':False,
        'boundary':['first cycle exact protected-Active parity','post-Active Expand unchanged through first fill','second cycle one ask-1 Passive source + TTL retention','second Active only after terminal zero-fill and +1 tick protected limit','no third cycle','no Target runtime/no winner action/no 8781/no scale']})
if __name__=='__main__':main()
