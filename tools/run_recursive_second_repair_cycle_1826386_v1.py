"""Research-only second Repair-cycle causal probe for market 1826386.

A = frozen one-Active conditional relay path.
B = identical through first Active Repair fill and first subsequently submitted+filled
    opposite SATELLITE_EXPAND. After that, the first newly submitted same-side
    ECONOMIC_CORE/SATELLITE_REPAIR carrier that terminal-zero-fills may relay once
    to current ask with the same side/role and no more than source remaining qty.

No recursive loop beyond this one second-cycle relay; no new direction authority.
"""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_passive_exhaustion_same_intent_route_relay_1823755_v1.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('staged_route_relay_second_cycle',_STAGED);rr=importlib.util.module_from_spec(sp);sp.loader.exec_module(rr)
else:
    import tools.run_passive_exhaustion_same_intent_route_relay_1823755_v1 as rr
v1=rr.v1;EPS=1e-9;TERMINAL=rr.TERMINAL;ACTIVE_WINDOW_MS=500
CELLS=('A_ONE_ACTIVE_BASELINE','B_SECOND_CYCLE_ACTIVE_RELAY')

class SecondCycleSim(rr.RouteRelaySim):
    def __init__(self,tape,enable_second:bool,trace_path):
        super().__init__(tape,rr.CELLS[2],trace_path)
        self.enable_second=bool(enable_second)
        self.first_active_fill_at=None;self.first_active_side=None
        self.post_active_expand_keys=set();self.post_active_expand_fill_at=None;self.post_active_expand_fill_key=None
        self.second_source_key=None;self.second_source_meta=None;self.second_pending=None
        self.second_relay_used=False;self.second_relay_key=None;self.second_relay_meta=None
        self.second_active_submitted_at=None;self.second_active_cancel_requested=False;self.second_block_reason=None
        self._prev_cum={}

    def _submit_role(self,t,side,role,p,q,proj,source):
        before_n=self.n
        ok=super()._submit_role(t,side,role,p,q,proj,source)
        if ok:
            key=f'{side}_{before_n}'
            if self.first_active_fill_at is not None and int(t)>=int(self.first_active_fill_at):
                if role=='SATELLITE_EXPAND' and side!=self.first_active_side:
                    self.post_active_expand_keys.add(key)
                if (self.post_active_expand_fill_at is not None and self.second_source_key is None and
                    side==self.first_active_side and role in {'ECONOMIC_CORE','SATELLITE_REPAIR'} and
                    int(t)>=int(self.post_active_expand_fill_at)):
                    self.second_source_key=key
                    self.second_source_meta={'key':key,'submittedAt':int(t),'side':side,'role':role,'price':float(p),'qty':float(q)}
        return ok

    def process(self,t):
        before={k:float(o.get('cum') or 0.0) for k,o in self.orders.items()}
        super().process(t)
        # First Active relay fill from inherited first-cycle route.
        if self.first_active_fill_at is None and self.relay_key and self.relay_meta and self.relay_meta.get('route')=='ACTIVE':
            o=self.orders.get(self.relay_key)
            if o and float(o.get('cum') or 0.0)>float(before.get(self.relay_key,0.0))+EPS:
                self.first_active_fill_at=int(t);self.first_active_side=str(o['side'])
        # First Expand that was submitted after first Active fill and then physically fills.
        if self.first_active_fill_at is not None and self.post_active_expand_fill_at is None:
            for key in list(self.post_active_expand_keys):
                o=self.orders.get(key)
                if o and float(o.get('cum') or 0.0)>float(before.get(key,0.0))+EPS:
                    self.post_active_expand_fill_at=int(t);self.post_active_expand_fill_key=key;break
        self._manage_second_active_remainder(t)

    def _refresh_slots(self,t):
        # Observe designated post-Expand Repair source before inherited release.
        if self.enable_second and self.second_source_key and self.second_pending is None and not self.second_relay_used:
            o=self.orders.get(self.second_source_key)
            if o:
                try:s=self.snap(o)
                except Exception:s={}
                st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
                if st in TERMINAL:
                    if st!='FILLED' and cum<=EPS:
                        remaining=max(0.0,float(o.get('qty') or 0.0)-cum)
                        self.second_pending={**(self.second_source_meta or {}),'terminalAt':int(t),'terminalStatus':st,'remainingQty':remaining}
                    else:
                        self.second_block_reason='SECOND_SOURCE_NOT_ZERO_FILL'
        super()._refresh_slots(t)
        self._manage_second_active_remainder(t)

    def _submit_second_active(self,t,qv):
        src=self.second_pending
        if not src:return False
        side=str(src['side']);role=str(src['role']);qty=float(src['remainingQty']);ask=float(qv[side]['ask']);bid=float(qv[side]['bid'])
        if qty<=EPS:
            self.second_block_reason='ZERO_SOURCE_REMAINING';return False
        if len(self.slot_key)>=self.max_slots:
            self.second_block_reason='NO_FREE_SLOT';return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:
            self.second_block_reason='NO_FREE_SLOT';return False
        n=self.n;self.n+=1;ex=rr.isp.base.v2.base.ex;native_side,native_price=ex.native_order(side,ask)
        try:
            if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
        except Exception as exc:
            self.second_block_reason='ACTIVE_SUBMIT_EXCEPTION:'+str(exc);return False
        key=f'{side}_{n}';self.orders[key]={'n':n,'side':side,'price':ask,'qty':qty,'cum':0.0,'placed':int(t),'status':'NEW','cancelRequested':False}
        self.placeHist.append((int(t),side,qty,ask));self.submits+=1;self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,ask,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        self.detail_outcomes[key]={'decisionId':self.detail_index,'key':key,'role':role,'side':side,'route':'SECOND_CYCLE_ACTIVE_RELAY',
                                   'submittedAt':int(t),'submittedPrice':ask,'submittedQty':qty,'fills':[],'terminal':None}
        opp='DOWN' if side=='UP' else 'UP';oq=sum(float(a) for a,_ in self.un[opp]);avg=self.unmatched_avg(opp) if oq>EPS else None
        self.second_relay_used=True;self.second_relay_key=key;self.second_active_submitted_at=int(t)
        self.second_relay_meta={'route':'ACTIVE','sourceKey':src['key'],'submittedAt':int(t),'side':side,'role':role,'price':ask,'qty':qty,
                                'bidAtRelay':bid,'askAtRelay':ask,'oppositeUnmatchedQty':oq,'oppositeUnmatchedAverage':avg,
                                'pairSum':(avg+ask) if avg is not None else None,'pairOverage':(avg+ask-1.0) if avg is not None else None,'submitRc':rc}
        self.slot_history.append({'t':int(t),'event':'SECOND_CYCLE_ACTIVE_RELAY_SUBMIT','sourceKey':src['key'],'key':key,'slotId':int(free),
                                  'side':side,'role':role,'price':ask,'qty':qty,'submitRc':rc})
        return True

    def _manage_second_active_remainder(self,t):
        if not self.second_relay_key or self.second_active_cancel_requested:return
        o=self.orders.get(self.second_relay_key)
        if not o or self.second_active_submitted_at is None:return
        try:s=self.snap(o)
        except Exception:return
        if rr.isp.base.v2.base.live(s.get('status')) and int(t)-int(self.second_active_submitted_at)>=ACTIVE_WINDOW_MS:
            cur=self.bt.orders(0).get(o['n'])
            if cur is not None and bool(cur.cancellable):
                try:
                    self.bt.cancel(0,o['n'],False);o['cancelRequested']=True;self.second_active_cancel_requested=True
                    self.slot_history.append({'t':int(t),'event':'SECOND_CYCLE_ACTIVE_CANCEL_REMAINDER','key':self.second_relay_key})
                except Exception:pass

    def _open_one_option(self,t,qv,end):
        if self.enable_second and self.second_pending is not None and not self.second_relay_used:
            if int(end)-int(t)<=rr.isp.base.v2.NO_NEW_EXPOSURE_MS:
                self.second_block_reason='LATE_180S';self.second_pending=None
            else:
                ok=self._submit_second_active(t,qv);self.second_pending=None
                if ok:return
        return super()._open_one_option(t,qv,end)

    def run_second(self):
        r=super().run_route()
        outcome=next((x for x in r.get('orderOutcomes',[]) if x.get('key')==self.second_relay_key),None)
        if outcome is None and self.second_relay_key in self.detail_outcomes:outcome=self.detail_outcomes[self.second_relay_key]
        r.update({'secondCycleEnabled':self.enable_second,'firstActiveFillAt':self.first_active_fill_at,'firstActiveSide':self.first_active_side,
                  'postActiveExpandKeys':sorted(self.post_active_expand_keys),'postActiveExpandFillAt':self.post_active_expand_fill_at,
                  'postActiveExpandFillKey':self.post_active_expand_fill_key,'secondSourceKey':self.second_source_key,
                  'secondSourceMeta':self.second_source_meta,'secondPendingAtEnd':self.second_pending,'secondRelayUsed':self.second_relay_used,
                  'secondRelayKey':self.second_relay_key,'secondRelayMeta':self.second_relay_meta,'secondRelayOutcome':outcome,
                  'secondActiveRemainderCancelRequested':self.second_active_cancel_requested,'secondBlockReason':self.second_block_reason,
                  'automaticScaleAllowed':False})
        return r

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--market-id',type=int,default=1826386);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=1826386:ap.error('Preregistered only for 1826386')
    op=Path(a.output).resolve()
    if op.exists():ap.error('Do not overwrite')
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'))
    if ref.get('marketId')!=1826386 or ref.get('cell')!='C_ACTIVE_ASK_RELAY_500MS':raise RuntimeError('Reference must be frozen 1826386 first-Active row')
    fields=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized',
            'roleSubmits','roleFills','roleFillQty','reanchors','economicRepairQty','economicOverflowQty')
    trace_dir=op.parent/(op.stem+'_traces');trace_dir.mkdir(parents=True,exist_ok=False);rows=[]
    with tempfile.TemporaryDirectory(prefix='second_cycle_1826386_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(r['marketId']):r for r in json.loads(z.read('cohort.json'))['rows']};z.extract(f'tapes/{a.market_id}.json.xz',root)
        tape=root/'tapes'/f'{a.market_id}.json.xz'
        if v1.sha256(tape)!=ref['tapeSha256']:raise RuntimeError('Tape mismatch')
        winner=str(cohort[a.market_id]['winner']).upper()
        for cell,enabled in [(CELLS[0],False),(CELLS[1],True)]:
            tp=trace_dir/f'{a.market_id}_{cell}.jsonl';sim=SecondCycleSim(tape,enabled,tp)
            try:r=sim.run_second()
            finally:sim.close()
            r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional']
            row={'marketId':a.market_id,'cell':cell,'winnerPostHocOnly':winner,'tapeSha256':ref['tapeSha256'],'decisionTracePath':str(tp),**r}
            if cell==CELLS[0]:
                row['baselineParity']={f:eq(row[f],ref[f]) for f in fields}
                if not all(row['baselineParity'].values()):raise RuntimeError('A parity fail '+str(row['baselineParity']))
            rows.append(row);v1.write_json(trace_dir/f'{a.market_id}_{cell}_result.json',row)
            so=row.get('secondRelayOutcome') or {};sm=row.get('secondRelayMeta') or {}
            print(json.dumps({'cell':cell,'submits':row['submits'],'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'repairQty':row['economicRepairQty'],
                              'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'firstActiveFillAt':row.get('firstActiveFillAt'),
                              'postActiveExpandFillKey':row.get('postActiveExpandFillKey'),'postActiveExpandFillAt':row.get('postActiveExpandFillAt'),
                              'secondSource':row.get('secondSourceMeta'),'secondRelayUsed':row.get('secondRelayUsed'),'secondRoute':sm.get('route'),
                              'secondPrice':sm.get('price'),'secondPairSum':sm.get('pairSum'),'secondTerminal':(so.get('terminal') or {}).get('status'),
                              'secondFillQty':sum(float(x.get('confirmedQty') or 0) for x in so.get('fills',[])),
                              'secondRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in so.get('fills',[])),
                              'secondOverflow':sum(float(x.get('overflowQty') or 0) for x in so.get('fills',[])),
                              'secondBlock':row.get('secondBlockReason')},allow_nan=False),flush=True)
    # Pre-trigger path contract: baseline and B must share first Active fill and post-Active Expand fill identity/timing.
    A,B=rows
    contract={'firstActiveFillSame':A.get('firstActiveFillAt')==B.get('firstActiveFillAt'),
              'firstActiveSideSame':A.get('firstActiveSide')==B.get('firstActiveSide'),
              'postActiveExpandFillSame':A.get('postActiveExpandFillAt')==B.get('postActiveExpandFillAt') and A.get('postActiveExpandFillKey')==B.get('postActiveExpandFillKey')}
    if not all(contract.values()):raise RuntimeError('Pre-trigger contract fail '+str(contract))
    v1.write_json(op,{'version':'RECURSIVE_SECOND_REPAIR_CYCLE_1826386_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,
                      'marketId':a.market_id,'rows':rows,'preTriggerContract':contract,'bundleSha256':v1.sha256(a.bundle),
                      'referenceSha256':v1.sha256(a.reference),'automaticScaleAllowed':False,
                      'boundary':['one existing first Active relay unchanged','first subsequently submitted+filled opposite Expand unchanged','one second-cycle Active max after later Repair/Core zero-fill','same second-source side/role/qty ceiling','no new responsibility/direction authority','500ms active remainder','<=180s','realistic HFT/no dream fill/no 8781','winner posthoc only','no Target runtime']})
if __name__=='__main__':main()
