"""Preregistered third-cycle execution-ladder causal probe on 1823755.
A = exact frozen two-cycle full-ladder baseline.
B = after first post-second-Repair Expand fill, next Repair/Core intent gets one ask-1tick Passive GTX + TTL retention.
C = same B; terminal zero-fill may hand off once to same-intent Active with +1 venue tick protection.
No fourth cycle.
"""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_recursive_execution_ladder_replication3_v1.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('staged_recursive3_third',_STAGED);r3=importlib.util.module_from_spec(sp);sp.loader.exec_module(r3)
else:
    import tools.run_recursive_execution_ladder_replication3_v1 as r3
rr=r3.rr;v1=r3.v1;EPS=1e-9;TICK=0.01;TERMINAL=r3.rec.TERMINAL;ACTIVE_WINDOW_MS=500
MID=1823755
CELLS=('A_TWO_CYCLE_BASELINE','B_THIRD_CYCLE_INSIDE_SPREAD_TTL','C_THIRD_CYCLE_FULL_LADDER')
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class ThirdCycleSim(r3.RecursiveProtectedSim):
    def __init__(self,tape,cell,trace_path):
        # Parent full second-cycle ladder in every cell; third_cell alone may diverge later.
        super().__init__(tape,r3.CELLS[2],trace_path)
        self.third_cell=cell
        self.second_repair_fill_at=None;self.second_repair_side=None;self.second_repair_key=None
        self.post_second_expand_keys=set();self.post_second_expand_fill_at=None;self.post_second_expand_fill_key=None
        self.third_arm=False;self.third_qv=None;self.third_side=None;self.third_role=None;self.third_t=None
        self.third_source_used=False;self.third_source_key=None;self.third_source_meta=None;self.third_cancel_suppressed=0
        self.third_source_terminal=None;self.third_pending_active=None
        self.third_active_used=False;self.third_active_key=None;self.third_active_meta=None;self.third_active_submitted_at=None;self.third_active_cancel=False
        self.third_block=None

    def process(self,t):
        before={k:float(o.get('cum') or 0.0) for k,o in self.orders.items()}
        super().process(t)
        if self.second_repair_fill_at is None:
            for key in (self.second_source_key,self.second_active_key):
                if not key:continue
                o=self.orders.get(key)
                if o and float(o.get('cum') or 0.0)>float(before.get(key,0.0))+EPS:
                    self.second_repair_fill_at=int(t);self.second_repair_side=str(o['side']);self.second_repair_key=key;break
        if self.second_repair_fill_at is not None and self.post_second_expand_fill_at is None:
            for key in list(self.post_second_expand_keys):
                o=self.orders.get(key)
                if o and float(o.get('cum') or 0.0)>float(before.get(key,0.0))+EPS:
                    self.post_second_expand_fill_at=int(t);self.post_second_expand_fill_key=key;break
        self._manage_third_active(t)

    def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
        original=super()._candidate_from_levels(side,require_pair,require_budget)
        if (not self.third_arm or self.third_source_used or self.third_cell==CELLS[0] or original is None or
            side!=self.third_side or self.third_role not in REPAIR_ROLES):return original
        p0,q0,proj=original;qv=self.third_qv;bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);price=round(ask-TICK,10)
        used={round(float(p),10) for p in self._used_prices(side)}
        if not (EPS<bid<price<ask-EPS and price not in used and price>float(p0)+EPS):
            self.third_block='NO_THIRD_INSIDE_SPREAD_IMPROVEMENT';return original
        qty=1.0/price
        if qty>12+EPS:
            self.third_block='THIRD_QTY_PHYSICAL_BLOCK';return original
        opp='DOWN' if side=='UP' else 'UP';lots=[(float(q),float(p)) for q,p in self.un[opp]];gap=sum(q for q,_ in lots)
        avg=sum(q*p for q,p in lots)/gap if gap>EPS else None
        self.third_source_meta={'t':int(self.third_t),'side':side,'role':self.third_role,'inheritedPrice':float(p0),'inheritedQty':float(q0),
            'probePrice':price,'probeQty':qty,'bidAtDecision':bid,'askAtDecision':ask,'oppositeUnmatchedQty':gap,'oppositeUnmatchedAverage':avg,
            'pairSum':(avg+price) if avg is not None else None,'pairOverage':(avg+price-1) if avg is not None else None,
            'route':'PASSIVE_GTX_POST_ONLY','intervention':'THIRD_CYCLE_ASK_MINUS_ONE_TICK'}
        return price,qty,proj

    def _submit_role(self,t,side,role,p,q,proj,source):
        before_n=self.n;ok=super()._submit_role(t,side,role,p,q,proj,source)
        if not ok:return ok
        key=f'{side}_{before_n}'
        if self.second_repair_fill_at is not None and int(t)>=self.second_repair_fill_at and role=='SATELLITE_EXPAND' and side!=self.second_repair_side:
            self.post_second_expand_keys.add(key)
        if (self.third_arm and self.third_cell!=CELLS[0] and not self.third_source_used and self.third_source_meta and
            side==self.third_source_meta['side'] and role==self.third_source_meta['role'] and abs(float(p)-self.third_source_meta['probePrice'])<=EPS):
            self.third_source_used=True;self.third_source_key=key;self.third_source_meta['key']=key;self.third_source_meta['submittedAt']=int(t)
            if key in self.detail_outcomes:self.detail_outcomes[key].update({'route':'THIRD_CYCLE_PASSIVE','thirdCycleInsideSpread':True})
        return ok

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(sid)
        if (self.third_cell in {CELLS[1],CELLS[2]} and key==self.third_source_key and reason in {'CORE_INVALIDATED','SATELLITE_FRONTIER_REANCHOR'}):
            o=self.orders.get(key)
            if o is not None and int(t)-int(o['placed'])<int(rr.isp.base.v2.base.TTL):
                self.third_cancel_suppressed+=1;return False
        return super()._request_cancel(t,sid,reason)

    def _refresh_slots(self,t):
        if self.third_source_key and self.third_source_terminal is None:
            o=self.orders.get(self.third_source_key)
            if o:
                try:s=self.snap(o)
                except Exception:s={}
                st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
                if st in TERMINAL:
                    self.third_source_terminal={'t':int(t),'status':st,'cum':cum}
                    if self.third_cell==CELLS[2] and st!='FILLED' and cum<=EPS:
                        self.third_pending_active={'sourceKey':self.third_source_key,'terminalAt':int(t),'side':str(o['side']),
                            'role':str(self.key_role.get(self.third_source_key,'UNKNOWN')),'remainingQty':max(0.0,float(o.get('qty') or 0.0)-cum),'sourcePrice':float(o['price'])}
        super()._refresh_slots(t);self._manage_third_active(t)

    def _submit_third_active(self,t,qv):
        src=self.third_pending_active
        if not src:return False
        side=src['side'];role=src['role'];qty=float(src['remainingQty']);bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);limit=round(min(0.99,ask+TICK),10)
        if qty<=EPS:self.third_block='ZERO_THIRD_REMAINING';return False
        if limit<=ask+EPS:self.third_block='THIRD_PROTECTION_NOT_AVAILABLE';return False
        if len(self.slot_key)>=self.max_slots:self.third_block='NO_THIRD_ACTIVE_SLOT';return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:self.third_block='NO_THIRD_ACTIVE_SLOT';return False
        n=self.n;self.n+=1;ex=rr.isp.base.v2.base.ex;ns,np=ex.native_order(side,limit)
        try:
            if ns=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),np,qty,ex.hbt.GTC,ex.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),np,qty,ex.hbt.GTC,ex.LIMIT,False))
        except Exception as exc:self.third_block='THIRD_ACTIVE_EXCEPTION:'+str(exc);return False
        key=f'{side}_{n}';self.orders[key]={'n':n,'side':side,'price':limit,'qty':qty,'cum':0.,'placed':int(t),'status':'NEW','cancelRequested':False}
        self.placeHist.append((int(t),side,qty,limit));self.submits+=1;self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,limit,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        self.detail_outcomes[key]={'decisionId':self.detail_index,'key':key,'role':role,'side':side,'route':'THIRD_CYCLE_ACTIVE_RELAY_PROTECTED_1T',
            'submittedAt':int(t),'submittedPrice':limit,'submittedQty':qty,'fills':[],'terminal':None}
        opp='DOWN' if side=='UP' else 'UP';oq=sum(float(a) for a,_ in self.un[opp]);avg=self.unmatched_avg(opp) if oq>EPS else None
        self.third_active_used=True;self.third_active_key=key;self.third_active_submitted_at=int(t)
        self.third_active_meta={'sourceKey':src['sourceKey'],'submittedAt':int(t),'side':side,'role':role,'decisionAsk':ask,'price':limit,'limitPrice':limit,
            'priceProtectionTicks':1,'qty':qty,'bidAtRelay':bid,'askAtRelay':ask,'oppositeUnmatchedQty':oq,'oppositeUnmatchedAverage':avg,
            'pairSum':(avg+limit) if avg is not None else None,'pairOverage':(avg+limit-1) if avg is not None else None,'submitRc':rc}
        self.slot_history.append({'t':int(t),'event':'THIRD_CYCLE_ACTIVE_RELAY_SUBMIT','sourceKey':src['sourceKey'],'key':key,'slotId':int(free),
            'side':side,'role':role,'decisionAsk':ask,'price':limit,'priceProtectionTicks':1,'qty':qty,'submitRc':rc})
        return True

    def _manage_third_active(self,t):
        if not self.third_active_key or self.third_active_cancel:return
        o=self.orders.get(self.third_active_key)
        if not o or self.third_active_submitted_at is None:return
        try:s=self.snap(o)
        except Exception:return
        if rr.isp.base.v2.base.live(s.get('status')) and int(t)-self.third_active_submitted_at>=ACTIVE_WINDOW_MS:
            cur=self.bt.orders(0).get(o['n'])
            if cur is not None and bool(cur.cancellable):
                try:self.bt.cancel(0,o['n'],False);o['cancelRequested']=True;self.third_active_cancel=True
                except Exception:pass

    def _open_one_option(self,t,qv,end):
        if self.third_pending_active is not None and not self.third_active_used and self.third_cell==CELLS[2]:
            if int(end)-int(t)<=rr.isp.base.v2.NO_NEW_EXPOSURE_MS:self.third_block='LATE_180S';self.third_pending_active=None
            else:
                ok=self._submit_third_active(t,qv);self.third_pending_active=None
                if ok:return
        arm=False
        if (self.third_cell!=CELLS[0] and self.post_second_expand_fill_at is not None and not self.third_source_used and int(t)>=self.post_second_expand_fill_at):
            side,role,_,_=rr.isp.base.MinimalPairRoleSim._role_decision(self,qv)
            if side==self.second_repair_side and role in REPAIR_ROLES:
                arm=True;self.third_arm=True;self.third_qv=qv;self.third_side=side;self.third_role=role;self.third_t=int(t)
        try:return super()._open_one_option(t,qv,end)
        finally:
            if arm:self.third_arm=False;self.third_qv=None;self.third_side=None;self.third_role=None;self.third_t=None

    def run_third(self):
        r=super().run_recursive_protected()
        so=next((x for x in r.get('orderOutcomes',[]) if x.get('key')==self.third_source_key),None)
        ao=next((x for x in r.get('orderOutcomes',[]) if x.get('key')==self.third_active_key),None)
        if ao is None and self.third_active_key in self.detail_outcomes:ao=self.detail_outcomes[self.third_active_key]
        r.update({'thirdCycleCell':self.third_cell,'secondRepairFillAt':self.second_repair_fill_at,'secondRepairSide':self.second_repair_side,'secondRepairKey':self.second_repair_key,
            'postSecondRepairExpandKeys':sorted(self.post_second_expand_keys),'postSecondRepairExpandFillAt':self.post_second_expand_fill_at,
            'postSecondRepairExpandFillKey':self.post_second_expand_fill_key,'thirdSourceUsed':self.third_source_used,'thirdSourceKey':self.third_source_key,
            'thirdSourceMeta':self.third_source_meta,'thirdSourceOutcome':so,'thirdSourceCancelSuppressed':self.third_cancel_suppressed,'thirdSourceTerminalObserved':self.third_source_terminal,
            'thirdActiveUsed':self.third_active_used,'thirdActiveKey':self.third_active_key,'thirdActiveMeta':self.third_active_meta,'thirdActiveOutcome':ao,
            'thirdActiveCancelRequested':self.third_active_cancel,'thirdBlock':self.third_block,'automaticScaleAllowed':False})
        return r

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(eq(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def third_summary(r):
    sm=r.get('thirdSourceMeta') or {};so=r.get('thirdSourceOutcome') or {};am=r.get('thirdActiveMeta') or {};ao=r.get('thirdActiveOutcome') or {};sf=so.get('fills') or [];af=ao.get('fills') or []
    return {'postSecondExpandKey':r.get('postSecondRepairExpandFillKey'),'postSecondExpandFillAt':r.get('postSecondRepairExpandFillAt'),'sourceUsed':r.get('thirdSourceUsed'),
        'sourceSide':sm.get('side'),'sourceRole':sm.get('role'),'inheritedPrice':sm.get('inheritedPrice'),'passivePrice':sm.get('probePrice'),'passiveQty':sm.get('probeQty'),'passivePair':sm.get('pairSum'),
        'sourceTerminal':(so.get('terminal') or {}).get('status'),'sourceFillQty':sum(float(x.get('confirmedQty') or 0) for x in sf),'sourceRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in sf),
        'cancelSuppressed':r.get('thirdSourceCancelSuppressed'),'activeUsed':r.get('thirdActiveUsed'),'activeDecisionAsk':am.get('decisionAsk',am.get('askAtRelay')),
        'activeLimit':am.get('limitPrice',am.get('price')),'activePair':am.get('pairSum'),'activeTerminal':(ao.get('terminal') or {}).get('status'),
        'activeFillQty':sum(float(x.get('confirmedQty') or 0) for x in af),'activeRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in af),'activeActualPrices':[x.get('executionPriceFromInheritedSubstrate') for x in af],
        'block':r.get('thirdBlock')}
def prefix_sig(r,cut):
    keep={'ROLE_SLOT_SUBMIT','SAME_INTENT_ACTIVE_RELAY_SUBMIT','SECOND_CYCLE_ACTIVE_RELAY_SUBMIT','ROLE_FILL','SLOT_RELEASE','SLOT_CANCEL_REQUEST'}
    return [(x.get('t'),x.get('event'),x.get('key'),x.get('role'),x.get('side'),x.get('price'),x.get('qty'),x.get('reason')) for x in r.get('slotHistory',[]) if x.get('event') in keep and int(x.get('t') or 0)<=int(cut)]

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    op=Path(a.output).resolve();
    if op.exists():ap.error('Do not overwrite')
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'))
    if ref.get('marketId')!=MID or ref.get('cell')!='C_SECOND_CYCLE_FULL_LADDER':raise RuntimeError('wrong reference')
    fields=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','economicRepairQty','economicOverflowQty')
    trace_dir=op.parent/(op.stem+'_traces');trace_dir.mkdir(parents=True,exist_ok=False);rows=[]
    with tempfile.TemporaryDirectory(prefix='third_cycle_1823755_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};z.extract(f'tapes/{MID}.json.xz',root)
        tape=root/'tapes'/f'{MID}.json.xz';winner=str(co[MID]['winner']).upper()
        if v1.sha256(tape)!=ref['tapeSha256']:raise RuntimeError('tape mismatch')
        for cell in CELLS:
            tp=trace_dir/f'{MID}_{cell}.jsonl';sim=ThirdCycleSim(tape,cell,tp)
            try:r=sim.run_third()
            finally:sim.close()
            r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional'];row={'marketId':MID,'cell':cell,'winnerPostHocOnly':winner,'tapeSha256':ref['tapeSha256'],'decisionTracePath':str(tp),**r};rows.append(row);v1.write_json(trace_dir/f'{MID}_{cell}_result.json',row)
            if cell==CELLS[0]:
                row['baselineParity']={f:eq(row[f],ref[f]) for f in fields}
                if not all(row['baselineParity'].values()):raise RuntimeError('A parity failed '+str(row['baselineParity']))
            print(json.dumps({'cell':cell,'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'repairQty':row['economicRepairQty'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'third':third_summary(row)},allow_nan=False),flush=True)
    A,B,C=rows;cut=A.get('postSecondRepairExpandFillAt')
    if cut is None:raise RuntimeError('baseline has no post-second Expand fill')
    contract={'secondRepairFillSame':A.get('secondRepairFillAt')==B.get('secondRepairFillAt')==C.get('secondRepairFillAt'),
        'postSecondExpandSame':A.get('postSecondRepairExpandFillAt')==B.get('postSecondRepairExpandFillAt')==C.get('postSecondRepairExpandFillAt') and A.get('postSecondRepairExpandFillKey')==B.get('postSecondRepairExpandFillKey')==C.get('postSecondRepairExpandFillKey'),
        'prefixSameThroughPostSecondExpand':prefix_sig(A,cut)==prefix_sig(B,cut)==prefix_sig(C,cut)}
    if not all(contract.values()):raise RuntimeError('pre-third contract failed '+str(contract))
    sb,sc=third_summary(B),third_summary(C)
    for k in ('sourceUsed','sourceSide','sourceRole','inheritedPrice','passivePrice','passiveQty'):
        if not eq(sb[k],sc[k]):raise RuntimeError('B/C source mismatch '+k)
    if not sb['sourceUsed']:raise RuntimeError('third source not used')
    if sc['activeUsed']:
        if sc['sourceTerminal'] in (None,'FILLED') or sc['sourceFillQty']>EPS:raise RuntimeError('invalid third Active terminal')
        if abs(float(sc['activeLimit'])-float(sc['activeDecisionAsk'])-TICK)>1e-9:raise RuntimeError('third protection not one tick')
    out={'version':'THIRD_CYCLE_EXECUTION_LADDER_1823755_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'rows':rows,
        'preThirdCycleContract':contract,'bundleSha256':v1.sha256(a.bundle),'referenceSha256':v1.sha256(a.reference),'automaticScaleAllowed':False,
        'boundary':['two-cycle baseline exact parity','third begins only after first post-second-Repair Expand fill','one ask-1 Passive source + inherited TTL retention','protected Active only after explicit terminal zero-fill','no fourth cycle','no Target runtime/no winner action/no 8781/no scale']}
    v1.write_json(op,out)
if __name__=='__main__':main()
