from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter,defaultdict

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED_V7=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_role_separated_multislot_v7_monetary_credit_smoke.py'
if _STAGED_V7.exists():
    _spec=importlib.util.spec_from_file_location('frozen_v7',_STAGED_V7)
    v7=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(v7)
else:
    import tools.run_eth_role_separated_multislot_v7_monetary_credit_smoke as v7
v2=v7.v2
EPS=1e-9
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class RepairOverflowSplitSim(v7.RealizedCreditMultiSlotSim):
    """V8: exact Repair-reservation + authorized-overflow split before physical submit.

    No new strategy threshold is introduced. Aggregate unresolved Repair quota is bounded by
    authoritative unmatched debt. Any venue-min remainder is explicit Overflow and must reserve
    already-realized monetary risk credit before submit. Pending Repair never creates credit.
    """
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots)
        self.keyRepairQuotaRemaining={}
        self.keyRepairQuotaAuthorized={}
        self.keyOverflowQtyRemaining={}
        self.keyOverflowQtyAuthorized={}
        self.totalRepairQuotaAuthorized=0.0
        self.totalOverflowQtyAuthorized=0.0
        self.totalRepairAllocated=0.0
        self.totalOverflowRealized=0.0
        self.totalOverflowRiskConsumed=0.0
        self.unauthorizedOverflowQty=0.0
        self.repairQuotaExcessMax=0.0
        self.splitBlocks=Counter()
        self.splitEvents=[]

    def _scope_debt_qty(self):
        if self.scopeSide is None:return 0.0
        return float(sum(float(a) for a,_ in self.un[self.scopeSide]))

    def _repair_side(self):
        if self.scopeSide is None:return None
        return 'DOWN' if self.scopeSide=='UP' else 'UP'

    def _reserved_repair_quota(self,repair_side=None):
        if self.scopeSide is None:return 0.0
        rs=repair_side or self._repair_side(); total=0.0
        for _,key,o,role in self._live_role_rows():
            if role not in REPAIR_ROLES or str(o['side'])!=rs:continue
            if self.key_scope_gen.get(key)!=self.scopeGeneration:continue
            total+=max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0)))
        return float(total)

    def _reserved_repair_overflow_risk(self):
        if self.scopeSide is None:return 0.0
        total=0.0
        for _,key,o,role in self._live_role_rows():
            if role not in REPAIR_ROLES or self.key_scope_gen.get(key)!=self.scopeGeneration:continue
            oq=max(0.0,float(self.keyOverflowQtyRemaining.get(key,0.0)))
            if oq>EPS:total+=oq*float(o['price'])
        return float(total)

    def _reserved_current_expand_risk(self):
        return float(super()._reserved_current_expand_risk()+self._reserved_repair_overflow_risk())

    def _audit_reservation(self):
        if self.scopeSide is None:return
        debt=self._scope_debt_qty(); reserved=self._reserved_repair_quota()
        self.repairQuotaExcessMax=max(self.repairQuotaExcessMax,max(0.0,reserved-debt))

    def _repair_split(self,side,p,q):
        debt=self._scope_debt_qty()
        reserved=self._reserved_repair_quota(side)
        available=max(0.0,debt-reserved)
        rq=min(float(q),available)
        if rq<=EPS:
            self.splitBlocks['NO_UNRESERVED_REPAIR_DEBT']+=1
            return None
        before=self._physical_floor()
        repair_floor=self._candidate_alone_floor(side,p,rq)
        if repair_floor<=before+EPS:
            self.splitBlocks['REPAIR_PORTION_NOT_FLOOR_IMPROVING']+=1
            return None
        oq=max(0.0,float(q)-rq)
        full_floor=self._candidate_alone_floor(side,p,q)
        overflow_risk=max(0.0,repair_floor-full_floor)
        # Overflow is a distinct new-exposure responsibility and may spend only already-realized credit.
        if overflow_risk>self._available_expand_risk_credit()+EPS:
            self.splitBlocks['OVERFLOW_MONETARY_CREDIT_INSUFFICIENT']+=1
            return None
        return {'repairQty':rq,'overflowQty':oq,'overflowRisk':overflow_risk,
                'repairOnlyFloor':repair_floor,'fullFloor':full_floor,
                'debt':debt,'reservedRepairBefore':reserved,'availableDebtBefore':available}

    def _candidate_from_levels_v8(self,side,role,require_pair):
        used=self._used_prices(side)
        for p in self._live_price_levels(side):
            p=v2.kprice(p)
            if p in used:continue
            q=1.0/p
            if require_pair and not self._pair_ok(side,p):
                self.veto['CORE_PAIR_ECONOMICS']+=1;continue
            split=None;proj=None
            if role in REPAIR_ROLES:
                split=self._repair_split(side,p,q)
                if split is None:continue
                proj=split['fullFloor']
            return float(p),float(q),proj,split
        return None

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before_n=self.n
        ok=super()._submit_role(t,side,role,p,q,proj)
        if not ok:return False
        key=f'{side}_{before_n}'
        if role in REPAIR_ROLES:
            sp=split or {'repairQty':0.0,'overflowQty':float(q),'overflowRisk':float(q)*float(p)}
            rq=float(sp['repairQty']);oq=float(sp['overflowQty'])
            self.keyRepairQuotaAuthorized[key]=rq;self.keyRepairQuotaRemaining[key]=rq
            self.keyOverflowQtyAuthorized[key]=oq;self.keyOverflowQtyRemaining[key]=oq
            self.totalRepairQuotaAuthorized+=rq;self.totalOverflowQtyAuthorized+=oq
            ev={'t':int(t),'event':'REPAIR_OVERFLOW_SPLIT_AUTHORIZED','key':key,'role':role,
                'generation':self.key_scope_gen.get(key),'side':side,'price':float(p),'orderQty':float(q),**sp,
                'availableRiskCreditAfterReservation':self._available_expand_risk_credit()}
            self.splitEvents.append(ev);self.slot_history.append(ev)
        elif role=='SATELLITE_EXPAND':
            self.keyRepairQuotaAuthorized[key]=0.0;self.keyRepairQuotaRemaining[key]=0.0
            self.keyOverflowQtyAuthorized[key]=float(q);self.keyOverflowQtyRemaining[key]=float(q)
        self._audit_reservation()
        return True

    def _open_one_option(self,t:int,qv,end:int):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:self.veto['LATE_180S']+=1;return
        if self._has_stale_scope_reservation():self.staleScopeWaits+=1;self.veto['STALE_SCOPE_RESERVATION_WAIT']+=1;return
        decision=self._role_decision(qv)
        if decision is None:self.veto['ROLE_WAIT']+=1;return
        side,role,require_pair,require_budget=decision
        if len(self._live_role_rows(side=side))>=self.max_slots:self.veto['SIDE_SLOT_CAP_FULL']+=1;return
        cand=self._candidate_from_levels_v8(side,role,require_pair)
        if cand is None:self.role_budget_blocks[role]+=1;return
        p,q,proj,split=cand
        if role=='SATELLITE_EXPAND':
            risk_cost=max(0.0,self._physical_floor()-self._candidate_alone_floor(side,p,q))
            if self._available_expand_risk_credit()+EPS<risk_cost:
                self.expandCreditBlocks+=1;self.veto['EXPAND_MONETARY_CREDIT_INSUFFICIENT']+=1;return
        self._submit_role_v8(t,side,role,p,q,proj,split)

    def process(self,t):
        old_scope=self.scopeSide;old_gen=int(self.scopeGeneration);floor_before=self._physical_floor()
        before={key:float(o.get('cum') or 0.0) for key,o in self.orders.items()}
        # Bypass V7 credit logic; use the same physical HFT/order accounting from frozen V2.
        v2.TargetGroundedDistinctSlotSim.process(self,t)
        fill_rows=[]
        for key,o in self.orders.items():
            inc=float(o.get('cum') or 0.0)-float(before.get(key,0.0))
            if inc<=EPS:continue
            role=self.key_role.get(key,'UNASSIGNED')
            self.role_fills[role]+=1;self.role_fill_qty[role]+=inc
            repair_alloc=0.0;overflow_fill=0.0
            if role in REPAIR_ROLES:
                rem_r=max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0)))
                repair_alloc=min(float(inc),rem_r)
                overflow_fill=max(0.0,float(inc)-repair_alloc)
                self.keyRepairQuotaRemaining[key]=max(0.0,rem_r-repair_alloc)
                rem_o=max(0.0,float(self.keyOverflowQtyRemaining.get(key,0.0)))
                unauthorized=max(0.0,overflow_fill-rem_o)
                if unauthorized>EPS:self.unauthorizedOverflowQty+=unauthorized
                self.keyOverflowQtyRemaining[key]=max(0.0,rem_o-overflow_fill)
            elif role=='SATELLITE_EXPAND':
                overflow_fill=float(inc)
                rem_o=max(0.0,float(self.keyOverflowQtyRemaining.get(key,float(o.get('qty') or 0.0))))
                unauthorized=max(0.0,overflow_fill-rem_o)
                if unauthorized>EPS:self.unauthorizedOverflowQty+=unauthorized
                self.keyOverflowQtyRemaining[key]=max(0.0,rem_o-overflow_fill)
            self.totalRepairAllocated+=repair_alloc;self.totalOverflowRealized+=overflow_fill
            overflow_risk=overflow_fill*float(o['price'])
            if overflow_fill>EPS:self.totalOverflowRiskConsumed+=overflow_risk
            fill_rows.append((key,o,inc,role,repair_alloc,overflow_fill,overflow_risk))
            ev={'t':int(t),'event':'ROLE_FILL_SPLIT','key':key,'role':role,'generationAtSubmit':self.key_scope_gen.get(key),
                'side':o['side'],'price':float(o['price']),'fillInc':float(inc),'repairAllocated':repair_alloc,
                'overflowRealized':overflow_fill,'overflowRisk':overflow_risk}
            self.splitEvents.append(ev);self.slot_history.append(ev)

        # Consume old-scope monetary credit for realized overflow before any scope reset.
        if old_scope is not None:
            for key,o,inc,role,rq,oq,orisk in fill_rows:
                if oq>EPS and self.key_scope_gen.get(key)==old_gen:
                    self.scopeRiskCreditConsumed+=orisk
                    if role=='SATELLITE_EXPAND':self.expand_consumed_keys.add(key)

        new_side=self._unmatched_scope_side()
        # Realized Repair credit is based only on the quota actually allocated to old responsibility.
        realized_repair_credit=0.0
        if old_scope is not None and new_side==old_scope:
            rs='DOWN' if old_scope=='UP' else 'UP'
            for key,o,inc,role,rq,oq,orisk in fill_rows:
                if role in REPAIR_ROLES and rq>EPS and str(o['side'])==rs and self.key_scope_gen.get(key)==old_gen:
                    realized_repair_credit+=rq*(1.0-float(o['price']))
        self._sync_scope(int(t),new_side,floor_before)
        if realized_repair_credit>EPS and self.scopeSide==old_scope and self.scopeGeneration==old_gen:
            self.scopeRepairProgressClocks+=1;self.totalRepairProgressClocks+=1
            self.totalRepairCreditValue+=realized_repair_credit;self.scopeRiskCreditTotal+=realized_repair_credit
            ev={'t':int(t),'event':'CONFIRMED_REPAIR_ALLOCATED_CREDIT','generation':self.scopeGeneration,
                'scopeSide':self.scopeSide,'creditValue':realized_repair_credit,
                'riskCreditTotal':self.scopeRiskCreditTotal,'physicalFloor':self._physical_floor()}
            self.scope_credit_events.append(ev);self.slot_history.append(ev)
        self._audit_reservation()

    def run_v8(self,winner):
        updates=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(self.meta['firstReceivedMs']);v2.base.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in updates:
            t=int(u[1]);v2.base.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);self._refresh_slots(t)
            v2.base.apply(self.book,u);qv=v2.base.quotes(self.book)
            if not qv:continue
            self._risk_contract_if_needed(t);self._reanchor_stale(t);self._open_one_option(t,qv,end);self._sample_occupancy()
        end2=int(self.meta['lastReceivedMs']);v2.base.ex.advance_to(self.bt,end2);self.process(end2);self._refresh_slots(end2);self._sample_occupancy()
        win=str(winner).upper();pnl=float(self.inv.get(win,0.0)-self.cost);floor=self._physical_floor();best=float(max(self.inv.values())-self.cost)
        return {'submits':int(self.submits),'fillEvents':int(self.fills),'filledQty':float(self.inv['UP']+self.inv['DOWN']),
                'pnlDiagnosticOnly':pnl,'floor':floor,'best':best,'maxSimultaneousDistinctPrices':int(self.max_simultaneous_distinct_prices),
                'roleSubmits':dict(self.role_submits),'roleFills':dict(self.role_fills),'roleFillQty':{k:float(v) for k,v in self.role_fill_qty.items()},
                'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,'scopeBirths':int(self.scopeBirths),'scopeCompletions':int(self.scopeCompletions),'scopeFlips':int(self.scopeFlips),
                'scopeRiskCreditTotal':float(self.scopeRiskCreditTotal),'scopeRiskCreditConsumed':float(self.scopeRiskCreditConsumed),'scopeRiskCreditReserved':float(self._reserved_current_expand_risk()),
                'totalRepairProgressClocks':int(self.totalRepairProgressClocks),'totalRepairCreditValue':float(self.totalRepairCreditValue),
                'totalRepairQuotaAuthorized':float(self.totalRepairQuotaAuthorized),'totalRepairAllocated':float(self.totalRepairAllocated),
                'totalOverflowQtyAuthorized':float(self.totalOverflowQtyAuthorized),'totalOverflowRealized':float(self.totalOverflowRealized),'totalOverflowRiskConsumed':float(self.totalOverflowRiskConsumed),
                'unauthorizedOverflowQty':float(self.unauthorizedOverflowQty),'repairQuotaExcessMax':float(self.repairQuotaExcessMax),
                'splitBlocks':dict(self.splitBlocks),'vetoCounts':dict(self.veto),'splitEvents':self.splitEvents[:1200],'slotHistory':self.slot_history[:1800]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='role_multislot_v8_split_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=v7.RealizedCreditMultiSlotSim(tape,4)
            try:r7=ctl.run_v6(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'V7_MONETARY_CREDIT_CONTROL','winnerPostHocOnly':cr['winner'],**r7})
            sim=RepairOverflowSplitSim(tape,4)
            try:r8=sim.run_v8(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'V8_REPAIR_OVERFLOW_SPLIT','winnerPostHocOnly':cr['winner'],**r8})
            print(json.dumps({'progress':mid,'v7Pnl':r7['pnlDiagnosticOnly'],'v8Pnl':r8['pnlDiagnosticOnly'],'v7Floor':r7['floor'],'v8Floor':r8['floor'],
                              'v7Fills':r7['fillEvents'],'v8Fills':r8['fillEvents'],'v8Submits':r8['submits'],'maxDistinct':r8['maxSimultaneousDistinctPrices'],
                              'repairAllocated':r8['totalRepairAllocated'],'overflow':r8['totalOverflowRealized'],'unauthOverflow':r8['unauthorizedOverflowQty'],'quotaExcess':r8['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell'].startswith('V7_')};n={r['marketId']:r for r in rows if r['cell'].startswith('V8_')}
        cmp=[]
        for mid in mids:
            a0=c[mid];b=n[mid]
            cmp.append({'marketId':mid,'pnlDelta':b['pnlDiagnosticOnly']-a0['pnlDiagnosticOnly'],'floorDelta':b['floor']-a0['floor'],
                        'fillRetention':(b['fillEvents']/a0['fillEvents']) if a0['fillEvents'] else None,'submitRetention':(b['submits']/a0['submits']) if a0['submits'] else None})
        continuation=[m for m in (1945898,1946317) if m in n]
        liveness_pass=all(c[m]['fillEvents']<=0 or n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in continuation)
        correctness_pass=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids)
        out={'version':'ETH_ROLE_SEPARATED_DISTINCT_MULTISLOT_V8_REPAIR_OVERFLOW_SPLIT_SMOKE','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,
             'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'allMarketsCompleted':len(rows)==2*len(mids),'correctnessSplitPass':correctness_pass,
                      'continuationLivenessRetention50pctResearchGate':liveness_pass,
                      'note50pct':'research anti-regression gate only; not Target-derived runtime threshold'},
             'boundary':['V7 live-book distinct-price generation frozen','no new hard strategy safety threshold','aggregate unresolved Repair quota <= authoritative debt','venue-min excess is explicit Overflow requiring already-realized monetary credit','pending Repair gives zero credit','Repair-first allocation then authorized Overflow exactly','250ms entry/response, risk queue, <=180s no new exposure','winner post-hoc only','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp,'candidateSummary':[{'marketId':m,'pnl':n[m]['pnlDiagnosticOnly'],'floor':n[m]['floor'],'fills':n[m]['fillEvents'],'submits':n[m]['submits'],'maxDistinct':n[m]['maxSimultaneousDistinctPrices'],'repairAllocated':n[m]['totalRepairAllocated'],'overflow':n[m]['totalOverflowRealized']} for m in mids]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
