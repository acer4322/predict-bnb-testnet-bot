"""Research-only recursive second-cycle execution ladder on market 1826386.
A = frozen one-Active baseline.
B = after first Active + first post-Active Expand fill, first renewed Repair intent gets ask-1 Passive GTX + TTL retention.
C = same B; only if second-cycle Passive terminal-zero-fills, one same-intent Active ask relay.
No third cycle and no automatic scale.
"""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_passive_exhaustion_same_intent_route_relay_1823755_v1.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('staged_route_relay_recursive_ladder',_STAGED);rr=importlib.util.module_from_spec(sp);sp.loader.exec_module(rr)
else:
    import tools.run_passive_exhaustion_same_intent_route_relay_1823755_v1 as rr
v1=rr.v1;EPS=1e-9;TICK=.01;TERMINAL=rr.TERMINAL;ACTIVE_WINDOW_MS=500
CELLS=('A_ONE_ACTIVE_BASELINE','B_SECOND_CYCLE_INSIDE_SPREAD_TTL','C_SECOND_CYCLE_LADDER_WITH_ACTIVE')
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class RecursiveLadderSim(rr.RouteRelaySim):
    def __init__(self,tape,cell,trace_path):
        super().__init__(tape,rr.CELLS[2],trace_path)
        self.second_cell=cell
        self.first_active_fill_at=None;self.first_active_side=None
        self.post_active_expand_keys=set();self.post_active_expand_fill_at=None;self.post_active_expand_fill_key=None
        self.second_arm=False;self.second_arm_qv=None;self.second_arm_side=None;self.second_arm_role=None;self.second_arm_t=None
        self.second_source_used=False;self.second_source_key=None;self.second_source_meta=None;self.second_source_cancel_suppressed=0
        self.second_source_terminal=None;self.second_pending_active=None
        self.second_active_used=False;self.second_active_key=None;self.second_active_meta=None;self.second_active_submitted_at=None;self.second_active_cancel=False
        self.second_block=None

    def process(self,t):
        before={k:float(o.get('cum') or 0) for k,o in self.orders.items()}
        super().process(t)
        if self.first_active_fill_at is None and self.relay_key and self.relay_meta and self.relay_meta.get('route')=='ACTIVE':
            o=self.orders.get(self.relay_key)
            if o and float(o.get('cum') or 0)>float(before.get(self.relay_key,0))+EPS:
                self.first_active_fill_at=int(t);self.first_active_side=str(o['side'])
        if self.first_active_fill_at is not None and self.post_active_expand_fill_at is None:
            for key in list(self.post_active_expand_keys):
                o=self.orders.get(key)
                if o and float(o.get('cum') or 0)>float(before.get(key,0))+EPS:
                    self.post_active_expand_fill_at=int(t);self.post_active_expand_fill_key=key;break
        self._manage_second_active(t)

    def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
        original=super()._candidate_from_levels(side,require_pair,require_budget)
        if (not self.second_arm or self.second_source_used or self.second_cell==CELLS[0] or original is None or
            side!=self.second_arm_side or self.second_arm_role not in REPAIR_ROLES):return original
        p0,q0,proj=original;qv=self.second_arm_qv;bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);price=round(ask-TICK,10)
        used={round(float(p),10) for p in self._used_prices(side)}
        if not (EPS<bid<price<ask-EPS and price not in used and price>float(p0)+EPS):
            self.second_block='NO_SECOND_INSIDE_SPREAD_IMPROVEMENT';return original
        qty=1.0/price
        if qty>12+EPS:
            self.second_block='SECOND_QTY_PHYSICAL_BLOCK';return original
        opp='DOWN' if side=='UP' else 'UP';lots=[(float(q),float(p)) for q,p in self.un[opp]];gap=sum(q for q,_ in lots)
        avg=sum(q*p for q,p in lots)/gap if gap>EPS else None
        self.second_source_meta={'t':int(self.second_arm_t),'side':side,'role':self.second_arm_role,
            'inheritedPrice':float(p0),'inheritedQty':float(q0),'probePrice':price,'probeQty':qty,
            'bidAtDecision':bid,'askAtDecision':ask,'oppositeUnmatchedQty':gap,'oppositeUnmatchedAverage':avg,
            'pairSum':(avg+price) if avg is not None else None,'pairOverage':(avg+price-1) if avg is not None else None,
            'route':'PASSIVE_GTX_POST_ONLY','intervention':'SECOND_CYCLE_ASK_MINUS_ONE_TICK'}
        return price,qty,proj

    def _submit_role(self,t,side,role,p,q,proj,source):
        before_n=self.n;ok=super()._submit_role(t,side,role,p,q,proj,source)
        if not ok:return ok
        key=f'{side}_{before_n}'
        if self.first_active_fill_at is not None and int(t)>=self.first_active_fill_at and role=='SATELLITE_EXPAND' and side!=self.first_active_side:
            self.post_active_expand_keys.add(key)
        if (self.second_arm and self.second_cell!=CELLS[0] and not self.second_source_used and self.second_source_meta and
            side==self.second_source_meta['side'] and role==self.second_source_meta['role'] and abs(float(p)-self.second_source_meta['probePrice'])<=EPS):
            self.second_source_used=True;self.second_source_key=key;self.second_source_meta['key']=key;self.second_source_meta['submittedAt']=int(t)
            if key in self.detail_outcomes:self.detail_outcomes[key].update({'route':'SECOND_CYCLE_PASSIVE','secondCycleInsideSpread':True})
        return ok

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(sid)
        if (self.second_cell in {CELLS[1],CELLS[2]} and key==self.second_source_key and reason in {'CORE_INVALIDATED','SATELLITE_FRONTIER_REANCHOR'}):
            o=self.orders.get(key)
            if o is not None and int(t)-int(o['placed'])<int(rr.isp.base.v2.base.TTL):
                self.second_source_cancel_suppressed+=1;return False
        return super()._request_cancel(t,sid,reason)

    def _refresh_slots(self,t):
        if self.second_source_key and self.second_source_terminal is None:
            o=self.orders.get(self.second_source_key)
            if o:
                try:s=self.snap(o)
                except Exception:s={}
                st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0)
                if st in TERMINAL:
                    self.second_source_terminal={'t':int(t),'status':st,'cum':cum}
                    if self.second_cell==CELLS[2] and st!='FILLED' and cum<=EPS:
                        qty=max(0.0,float(o.get('qty') or 0)-cum)
                        self.second_pending_active={'sourceKey':self.second_source_key,'terminalAt':int(t),'side':str(o['side']),
                            'role':str(self.key_role.get(self.second_source_key,'UNKNOWN')),'remainingQty':qty,'sourcePrice':float(o['price'])}
        super()._refresh_slots(t);self._manage_second_active(t)

    def _submit_second_active(self,t,qv):
        src=self.second_pending_active
        if not src:return False
        side=src['side'];role=src['role'];qty=float(src['remainingQty']);bid=float(qv[side]['bid']);ask=float(qv[side]['ask'])
        if qty<=EPS:self.second_block='ZERO_SECOND_REMAINING';return False
        if len(self.slot_key)>=self.max_slots:self.second_block='NO_SECOND_ACTIVE_SLOT';return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:self.second_block='NO_SECOND_ACTIVE_SLOT';return False
        n=self.n;self.n+=1;ex=rr.isp.base.v2.base.ex;ns,np=ex.native_order(side,ask)
        try:
            if ns=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),np,qty,ex.hbt.GTC,ex.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),np,qty,ex.hbt.GTC,ex.LIMIT,False))
        except Exception as exc:self.second_block='SECOND_ACTIVE_EXCEPTION:'+str(exc);return False
        key=f'{side}_{n}';self.orders[key]={'n':n,'side':side,'price':ask,'qty':qty,'cum':0.,'placed':int(t),'status':'NEW','cancelRequested':False}
        self.placeHist.append((int(t),side,qty,ask));self.submits+=1;self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,ask,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        self.detail_outcomes[key]={'decisionId':self.detail_index,'key':key,'role':role,'side':side,'route':'SECOND_CYCLE_ACTIVE_RELAY',
            'submittedAt':int(t),'submittedPrice':ask,'submittedQty':qty,'fills':[],'terminal':None}
        opp='DOWN' if side=='UP' else 'UP';oq=sum(float(a) for a,_ in self.un[opp]);avg=self.unmatched_avg(opp) if oq>EPS else None
        self.second_active_used=True;self.second_active_key=key;self.second_active_submitted_at=int(t)
        self.second_active_meta={'sourceKey':src['sourceKey'],'submittedAt':int(t),'side':side,'role':role,'price':ask,'qty':qty,
            'bidAtRelay':bid,'askAtRelay':ask,'oppositeUnmatchedQty':oq,'oppositeUnmatchedAverage':avg,
            'pairSum':(avg+ask) if avg is not None else None,'pairOverage':(avg+ask-1) if avg is not None else None,'submitRc':rc}
        self.slot_history.append({'t':int(t),'event':'SECOND_CYCLE_ACTIVE_RELAY_SUBMIT','sourceKey':src['sourceKey'],'key':key,
            'slotId':int(free),'side':side,'role':role,'price':ask,'qty':qty,'submitRc':rc})
        return True

    def _manage_second_active(self,t):
        if not self.second_active_key or self.second_active_cancel:return
        o=self.orders.get(self.second_active_key)
        if not o or self.second_active_submitted_at is None:return
        try:s=self.snap(o)
        except Exception:return
        if rr.isp.base.v2.base.live(s.get('status')) and int(t)-self.second_active_submitted_at>=ACTIVE_WINDOW_MS:
            cur=self.bt.orders(0).get(o['n'])
            if cur is not None and bool(cur.cancellable):
                try:self.bt.cancel(0,o['n'],False);o['cancelRequested']=True;self.second_active_cancel=True
                except Exception:pass

    def _open_one_option(self,t,qv,end):
        if self.second_pending_active is not None and not self.second_active_used and self.second_cell==CELLS[2]:
            if int(end)-int(t)<=rr.isp.base.v2.NO_NEW_EXPOSURE_MS:self.second_block='LATE_180S';self.second_pending_active=None
            else:
                ok=self._submit_second_active(t,qv);self.second_pending_active=None
                if ok:return
        arm=False
        if (self.second_cell!=CELLS[0] and self.post_active_expand_fill_at is not None and not self.second_source_used and
            int(t)>=self.post_active_expand_fill_at):
            side,role,_,_=rr.isp.base.MinimalPairRoleSim._role_decision(self,qv)
            if side==self.first_active_side and role in REPAIR_ROLES:
                arm=True;self.second_arm=True;self.second_arm_qv=qv;self.second_arm_side=side;self.second_arm_role=role;self.second_arm_t=int(t)
        try:return super()._open_one_option(t,qv,end)
        finally:
            if arm:self.second_arm=False;self.second_arm_qv=None;self.second_arm_side=None;self.second_arm_role=None;self.second_arm_t=None

    def run_recursive(self):
        r=super().run_route()
        src_out=next((x for x in r.get('orderOutcomes',[]) if x.get('key')==self.second_source_key),None)
        act_out=next((x for x in r.get('orderOutcomes',[]) if x.get('key')==self.second_active_key),None)
        if act_out is None and self.second_active_key in self.detail_outcomes:act_out=self.detail_outcomes[self.second_active_key]
        r.update({'secondCycleCell':self.second_cell,'firstActiveFillAt':self.first_active_fill_at,'firstActiveSide':self.first_active_side,
            'postActiveExpandKeys':sorted(self.post_active_expand_keys),'postActiveExpandFillAt':self.post_active_expand_fill_at,
            'postActiveExpandFillKey':self.post_active_expand_fill_key,'secondSourceUsed':self.second_source_used,
            'secondSourceKey':self.second_source_key,'secondSourceMeta':self.second_source_meta,'secondSourceOutcome':src_out,
            'secondSourceCancelSuppressed':self.second_source_cancel_suppressed,'secondSourceTerminalObserved':self.second_source_terminal,
            'secondActiveUsed':self.second_active_used,'secondActiveKey':self.second_active_key,'secondActiveMeta':self.second_active_meta,
            'secondActiveOutcome':act_out,'secondActiveCancelRequested':self.second_active_cancel,'secondBlock':self.second_block,
            'automaticScaleAllowed':False})
        return r

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def prefix_sig(r,cut):
    keep={'ROLE_SLOT_SUBMIT','SAME_INTENT_ACTIVE_RELAY_SUBMIT','ROLE_FILL','SLOT_RELEASE','SLOT_CANCEL_REQUEST'}
    return [(x.get('t'),x.get('event'),x.get('key'),x.get('role'),x.get('side'),x.get('price'),x.get('qty'),x.get('reason')) for x in r.get('slotHistory',[]) if x.get('event') in keep and int(x.get('t') or 0)<=int(cut)]

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mid=1826386
    op=Path(a.output).resolve();
    if op.exists():ap.error('Do not overwrite')
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'))
    if ref.get('marketId')!=mid or ref.get('cell')!='C_ACTIVE_ASK_RELAY_500MS':raise RuntimeError('Wrong frozen reference')
    fields=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','economicRepairQty','economicOverflowQty')
    trace_dir=op.parent/(op.stem+'_traces');trace_dir.mkdir(parents=True,exist_ok=False);rows=[]
    with tempfile.TemporaryDirectory(prefix='recursive_ladder_1826386_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};z.extract(f'tapes/{mid}.json.xz',root)
        tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
        if v1.sha256(tape)!=ref['tapeSha256']:raise RuntimeError('Tape mismatch')
        for cell in CELLS:
            tp=trace_dir/f'{mid}_{cell}.jsonl';sim=RecursiveLadderSim(tape,cell,tp)
            try:r=sim.run_recursive()
            finally:sim.close()
            r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional'];row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,'tapeSha256':ref['tapeSha256'],'decisionTracePath':str(tp),**r}
            if cell==CELLS[0]:
                row['baselineParity']={f:eq(row[f],ref[f]) for f in fields}
                if not all(row['baselineParity'].values()):raise RuntimeError('A parity failed '+str(row['baselineParity']))
            rows.append(row);v1.write_json(trace_dir/f'{mid}_{cell}_result.json',row)
            so=row.get('secondSourceOutcome') or {};sm=row.get('secondSourceMeta') or {};ao=row.get('secondActiveOutcome') or {};am=row.get('secondActiveMeta') or {}
            print(json.dumps({'cell':cell,'submits':row['submits'],'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'repairQty':row['economicRepairQty'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],
                'postExpandKey':row.get('postActiveExpandFillKey'),'postExpandFillAt':row.get('postActiveExpandFillAt'),
                'secondSourceUsed':row.get('secondSourceUsed'),'secondInheritedPrice':sm.get('inheritedPrice'),'secondPassivePrice':sm.get('probePrice'),'secondPassivePair':sm.get('pairSum'),
                'secondPassiveTerminal':(so.get('terminal') or {}).get('status'),'secondPassiveFillQty':sum(float(x.get('confirmedQty') or 0) for x in so.get('fills',[])),
                'secondPassiveRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in so.get('fills',[])),'secondCancelSupp':row.get('secondSourceCancelSuppressed'),
                'secondActiveUsed':row.get('secondActiveUsed'),'secondActivePrice':am.get('price'),'secondActivePair':am.get('pairSum'),
                'secondActiveTerminal':(ao.get('terminal') or {}).get('status'),'secondActiveFillQty':sum(float(x.get('confirmedQty') or 0) for x in ao.get('fills',[])),
                'secondActiveRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in ao.get('fills',[])),'secondBlock':row.get('secondBlock')},allow_nan=False),flush=True)
    A,B,C=rows;cut=A.get('postActiveExpandFillAt')
    contract={'firstActiveFillSame':A.get('firstActiveFillAt')==B.get('firstActiveFillAt')==C.get('firstActiveFillAt'),
              'postActiveExpandFillSame':A.get('postActiveExpandFillAt')==B.get('postActiveExpandFillAt')==C.get('postActiveExpandFillAt') and A.get('postActiveExpandFillKey')==B.get('postActiveExpandFillKey')==C.get('postActiveExpandFillKey'),
              'prefixSameThroughPostExpandFill':prefix_sig(A,cut)==prefix_sig(B,cut)==prefix_sig(C,cut)}
    if not all(contract.values()):raise RuntimeError('Pre-second-cycle contract failed '+str(contract))
    if not (B.get('secondSourceUsed') and C.get('secondSourceUsed')):raise RuntimeError('Second source not materialized in B/C')
    if not eq({k:B.get('secondSourceMeta',{}).get(k) for k in ('side','role','inheritedPrice','probePrice','probeQty')},{k:C.get('secondSourceMeta',{}).get(k) for k in ('side','role','inheritedPrice','probePrice','probeQty')}):raise RuntimeError('B/C second source differs')
    v1.write_json(op,{'version':'RECURSIVE_EXECUTION_LADDER_SECOND_CYCLE_1826386_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'marketId':mid,'rows':rows,'preSecondCycleContract':contract,
        'bundleSha256':v1.sha256(a.bundle),'referenceSha256':v1.sha256(a.reference),'automaticScaleAllowed':False,
        'boundary':['first cycle frozen','post-Active Expand frozen through fill','second cycle ask-1 Passive GTX only','second Passive retained only to inherited TTL','Active only after second Passive terminal zero-fill','same side/role, Active qty<=source remaining','no third cycle','<=180s','realistic HFT/no dream fill/no 8781','no Target runtime','winner posthoc only','pair overage diagnostic only']})
if __name__=='__main__':main()
