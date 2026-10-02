from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter,defaultdict

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
HBT244=Path(__file__).resolve().parent/'hftbacktest_244'
if HBT244.exists() and str(HBT244) not in sys.path: sys.path.insert(0,str(HBT244))
_STAGED_V2=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_target_grounded_distinct_multislot_v2_smoke.py'
if _STAGED_V2.exists():
    _spec=importlib.util.spec_from_file_location('frozen_distinct_v2',_STAGED_V2)
    v2=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(v2)
else:
    import tools.run_eth_target_grounded_distinct_multislot_v2_smoke as v2

EPS=1e-9

class RealizedCreditMultiSlotSim(v2.TargetGroundedDistinctSlotSim):
    """Role-separated multi-slot with responsibility-scoped *realized* credit.

    Key experiment:
    - Pending Repair carriers provide zero expansion credit.
    - Scope birth provides exactly one confirmation-tranche credit.
    - Each confirmed Repair payment clock that improves physical Floor and leaves the same
      unmatched-debt responsibility live creates one additional expansion-tranche credit.
    - SATELLITE_EXPAND reserves a credit at submit; terminal no-fill implicitly releases it;
      first confirmed fill consumes it once for that carrier.
    - Repair/Core admission is evaluated candidate-alone against the current responsibility
      birth Floor, not by assuming other passive Repair orders will fill.
    - Responsibility identity is FIFO unmatched-debt side; clear/flip starts a fresh scope.
    """
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots)
        self.max_slots=int(max_slots)
        self.key_role:dict[str,str]={}
        self.key_scope_gen:dict[str,int]={}
        self.role_submits=Counter();self.role_fills=Counter();self.role_fill_qty=defaultdict(float)
        self.role_cancel_requests=Counter();self.role_budget_blocks=Counter()
        self.marginal_pair_credit=defaultdict(float);self.marginal_pair_risk_spend=defaultdict(float)
        self.scopeSide=None;self.scopeFloorAnchor=None;self.scopeGeneration=0
        self.scopeBirths=0;self.scopeCompletions=0;self.scopeFlips=0
        self.scopeRiskCreditTotal=0.0;self.scopeRiskCreditConsumed=0.0;self.totalRepairCreditValue=0.0
        self.scopeRepairProgressClocks=0;self.totalRepairProgressClocks=0
        self.expand_consumed_keys=set();self.scope_credit_events=[]
        self.staleScopeCancelRequests=0;self.staleScopeWaits=0
        self.scopeBirthOptionBlocks=0;self.expandCreditBlocks=0
        self.corePreservedClocks=0;self.repairBundleProjectionMin=None

    def _physical_floor(self):
        return float(min(self.inv['UP'],self.inv['DOWN'])-self.cost)

    def _unmatched_scope_side(self):
        uq=sum(float(a) for a,_ in self.un['UP']);dq=sum(float(a) for a,_ in self.un['DOWN'])
        if uq>EPS and dq<=EPS:return 'UP'
        if dq>EPS and uq<=EPS:return 'DOWN'
        return None

    def _state(self):
        u=float(self.inv['UP']);d=float(self.inv['DOWN'])
        if u<=EPS and d<=EPS:return 'EMPTY'
        if self.scopeSide is None:return 'PAIRED'
        return 'SCOPED'

    def _live_role_rows(self,role=None,side=None):
        out=[]
        for sid,key in self.slot_key.items():
            o=self.orders.get(key)
            if not o:continue
            r=self.key_role.get(key,'UNASSIGNED')
            if role is not None and r!=role:continue
            if side is not None and str(o['side'])!=side:continue
            out.append((sid,key,o,r))
        return out

    def _core_for_side(self,side:str):
        rows=self._live_role_rows('ECONOMIC_CORE',side)
        return rows[0] if rows else None

    def _request_cancel(self,t:int,sid:int,reason:str)->bool:
        key=self.slot_key.get(int(sid));role=self.key_role.get(key,'UNASSIGNED') if key else 'UNASSIGNED'
        ok=super()._request_cancel(t,sid,reason)
        if ok:
            self.role_cancel_requests[f'{role}:{reason}']+=1
            if reason.startswith('STALE_SCOPE'):self.staleScopeCancelRequests+=1
        return ok

    def _cancel_stale_scope_orders(self,t:int):
        current=int(self.scopeGeneration)
        for sid,key,o,role in list(self._live_role_rows()):
            gen=self.key_scope_gen.get(key,current)
            if role in {'ECONOMIC_CORE','SATELLITE_REPAIR','SATELLITE_EXPAND'} and gen!=current and not o.get('cancelRequested'):
                self._request_cancel(t,int(sid),'STALE_SCOPE_TRANSITION')

    def _has_stale_scope_reservation(self):
        current=int(self.scopeGeneration)
        for _,key,o,role in self._live_role_rows():
            if role in {'ECONOMIC_CORE','SATELLITE_REPAIR','SATELLITE_EXPAND'} and self.key_scope_gen.get(key,current)!=current:
                return True
        return False

    def _sync_scope(self,t:int,new_side,floor_before):
        old=self.scopeSide
        if old is None and new_side is not None:
            self.scopeGeneration+=1;self.scopeBirths+=1;self.scopeSide=new_side;self.scopeFloorAnchor=self._physical_floor()
            birth_credit=max(0.0,float(floor_before)-self._physical_floor())
            self.scopeRiskCreditTotal=birth_credit;self.scopeRiskCreditConsumed=0.0;self.scopeRepairProgressClocks=0
            self.scope_credit_events.append({'t':int(t),'event':'SCOPE_BIRTH_CREDIT','generation':self.scopeGeneration,'side':new_side,'creditValue':birth_credit,'anchor':self.scopeFloorAnchor})
            self.slot_history.append({'t':int(t),'event':'RESPONSIBILITY_SCOPE_BIRTH','generation':self.scopeGeneration,'scopeSide':new_side,'floorAnchor':self.scopeFloorAnchor})
            self._cancel_stale_scope_orders(t);return
        if old is not None and new_side is None:
            self.scopeCompletions+=1
            self.slot_history.append({'t':int(t),'event':'RESPONSIBILITY_SCOPE_COMPLETE','generation':self.scopeGeneration,'scopeSide':old,'terminalFloor':self._physical_floor()})
            self.scopeSide=None;self.scopeFloorAnchor=None;self.scopeRiskCreditTotal=0.0;self.scopeRiskCreditConsumed=0.0;self.scopeRepairProgressClocks=0
            self._cancel_stale_scope_orders(t);return
        if old is not None and new_side is not None and old!=new_side:
            self.scopeFlips+=1;self.scopeGeneration+=1;self.scopeBirths+=1;self.scopeSide=new_side;self.scopeFloorAnchor=self._physical_floor()
            birth_credit=max(0.0,float(floor_before)-self._physical_floor())
            self.scopeRiskCreditTotal=birth_credit;self.scopeRiskCreditConsumed=0.0;self.scopeRepairProgressClocks=0
            self.scope_credit_events.append({'t':int(t),'event':'SCOPE_FLIP_CREDIT','generation':self.scopeGeneration,'fromSide':old,'side':new_side,'creditValue':birth_credit,'anchor':self.scopeFloorAnchor})
            self.slot_history.append({'t':int(t),'event':'RESPONSIBILITY_SCOPE_FLIP','generation':self.scopeGeneration,'fromSide':old,'scopeSide':new_side,'floorAnchor':self.scopeFloorAnchor})
            self._cancel_stale_scope_orders(t)

    def process(self,t):
        old_scope=self.scopeSide
        floor_before=self._physical_floor()
        before={key:float(o.get('cum') or 0.0) for key,o in self.orders.items()}
        super().process(t)
        fill_rows=[]
        for key,o in self.orders.items():
            inc=float(o.get('cum') or 0.0)-float(before.get(key,0.0))
            if inc>EPS:
                role=self.key_role.get(key,'UNASSIGNED')
                self.role_fills[role]+=1;self.role_fill_qty[role]+=inc
                fill_rows.append((key,o,inc,role))
                self.slot_history.append({'t':int(t),'event':'ROLE_FILL','key':key,'role':role,'side':o['side'],'price':float(o['price']),'fillInc':inc,'cum':float(o['cum']),'scopeGenerationAtSubmit':self.key_scope_gen.get(key)})
        new_side=self._unmatched_scope_side()
        repair_progress=False
        if old_scope is not None and new_side==old_scope:
            repair_side='DOWN' if old_scope=='UP' else 'UP'
            if any(str(o['side'])==repair_side for _,o,_,_ in fill_rows) and self._physical_floor()>floor_before+EPS:
                repair_progress=True
        self._sync_scope(int(t),new_side,floor_before)
        if repair_progress and self.scopeSide==old_scope:
            credit_value=max(0.0,self._physical_floor()-float(floor_before))
            self.scopeRepairProgressClocks+=1;self.totalRepairProgressClocks+=1;self.totalRepairCreditValue+=credit_value;self.scopeRiskCreditTotal+=credit_value
            ev={'t':int(t),'event':'CONFIRMED_REPAIR_PROGRESS_CREDIT','generation':self.scopeGeneration,'scopeSide':self.scopeSide,'creditValue':credit_value,'riskCreditTotal':self.scopeRiskCreditTotal,'physicalFloor':self._physical_floor()}
            self.scope_credit_events.append(ev);self.slot_history.append(ev)
        for key,o,inc,role in fill_rows:
            if role=='SATELLITE_EXPAND' and self.key_scope_gen.get(key)==self.scopeGeneration:
                risk_spend=float(inc)*float(o['price'])
                self.scopeRiskCreditConsumed+=risk_spend
                self.expand_consumed_keys.add(key)
                ev={'t':int(t),'event':'EXPAND_RISK_CREDIT_CONSUMED','generation':self.scopeGeneration,'key':key,'riskSpend':risk_spend,'riskCreditConsumed':self.scopeRiskCreditConsumed,'riskCreditTotal':self.scopeRiskCreditTotal}
                self.scope_credit_events.append(ev);self.slot_history.append(ev)

    def _candidate_alone_floor(self,side,p,q):
        up=float(self.inv['UP']);dn=float(self.inv['DOWN']);cost=float(self.cost)+float(q)*float(p)
        if side=='UP':up+=float(q)
        else:dn+=float(q)
        return float(min(up,dn)-cost)

    def _repair_budget_ok(self,side,p,q):
        # Target-consistent partial Repair: a Repair carrier need not restore the whole scope
        # anchor in one fill. It is admissible when its candidate-alone realized Floor moves
        # strictly in the Repair direction. Pending siblings contribute zero credit.
        before=self._physical_floor();after=self._candidate_alone_floor(side,p,q)
        return after>before+EPS,after

    def _pair_edge(self,side,p,q):
        opp='DOWN' if side=='UP' else 'UP';oppq=sum(float(a) for a,_ in self.un[opp])
        if oppq<=EPS:return (0.0,0.0)
        avg=self.unmatched_avg(opp)
        if avg is None:return (0.0,0.0)
        edge=1.0-(float(avg)+float(p))
        return (float(max(0.0,edge))*float(q),float(max(0.0,-edge))*float(q))

    def _used_prices(self,side):
        return {v2.kprice(o['price']) for _,_,o,_ in self._live_role_rows(side=side)}

    def _candidate_from_levels(self,side:str,require_pair:bool,require_repair_budget:bool):
        used=self._used_prices(side)
        for p in self._live_price_levels(side):
            p=v2.kprice(p)
            if p in used:continue
            q=1.0/p
            if require_pair and not self._pair_ok(side,p):
                self.veto['CORE_PAIR_ECONOMICS']+=1;continue
            proj=None
            if require_repair_budget:
                ok,proj=self._repair_budget_ok(side,p,q)
                if not ok:
                    self.veto['REPAIR_CANDIDATE_SCOPE_BUDGET']+=1;continue
            return float(p),float(q),proj
        return None

    def _reserved_current_expand_risk(self):
        if self.scopeSide is None:return 0.0
        total=0.0
        for _,key,o,role in self._live_role_rows(role='SATELLITE_EXPAND'):
            if self.key_scope_gen.get(key)==self.scopeGeneration:
                rem=self._remaining(key)
                if rem>EPS:total+=float(rem)*float(o['price'])
        return float(total)

    def _available_expand_risk_credit(self):
        if self.scopeSide is None:return 0.0
        if self._has_stale_scope_reservation():return 0.0
        return max(0.0,float(self.scopeRiskCreditTotal)-float(self.scopeRiskCreditConsumed)-float(self._reserved_current_expand_risk()))

    def _role_decision(self,qv):
        signal=self._direction(qv);state=self._state()
        if state=='EMPTY':
            if self._live_role_rows(role='PROBE_CORE'):return None
            return signal,'PROBE_CORE',False,False
        if state=='PAIRED':
            if self._live_role_rows(role='SCOPE_BIRTH'):self.scopeBirthOptionBlocks+=1;return None
            return signal,'SCOPE_BIRTH',False,False
        repair_side='DOWN' if self.scopeSide=='UP' else 'UP'
        if self._core_for_side(repair_side) is None:
            return repair_side,'ECONOMIC_CORE',True,True
        if signal==self.scopeSide:
            if self._available_expand_risk_credit()<=EPS:
                self.expandCreditBlocks+=1;return None
            return signal,'SATELLITE_EXPAND',False,False
        return repair_side,'SATELLITE_REPAIR',False,True

    def _submit_role(self,t:int,side:str,role:str,p:float,q:float,proj):
        if self._last_new_receipt==int(t):self.veto['ONE_NEW_OPTION_PER_RECEIPT']+=1;return False
        if len(self.slot_key)>=self.max_slots:self.veto['GLOBAL_SLOT_CAP_FULL']+=1;return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:return False
        before_n=self.n;self.submit(int(t),side,float(p),float(q));key=f'{side}_{before_n}'
        assigned_gen=self.scopeGeneration if self.scopeSide is not None else self.scopeGeneration+1
        self.slot_key[int(free)]=key;self.key_role[key]=role;self.key_scope_gen[key]=int(assigned_gen);self._last_new_receipt=int(t);self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,p,q);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        self.slot_history.append({'t':int(t),'event':'ROLE_SLOT_SUBMIT','slotId':int(free),'key':key,'role':role,'scopeGeneration':int(assigned_gen),'side':side,'price':float(p),'qty':float(q),'candidateAloneFloor':proj,'scopeFloorAnchor':self.scopeFloorAnchor,'availableExpandRiskCreditBefore':self._available_expand_risk_credit() if role=='SATELLITE_EXPAND' else None,'source':'CURRENT_STRICT_PAST_LIVE_BOOK_REALIZED_CREDIT'})
        return True

    def _open_one_option(self,t:int,qv,end:int):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:self.veto['LATE_180S']+=1;return
        if self._has_stale_scope_reservation():self.staleScopeWaits+=1;self.veto['STALE_SCOPE_RESERVATION_WAIT']+=1;return
        decision=self._role_decision(qv)
        if decision is None:self.veto['ROLE_WAIT']+=1;return
        side,role,require_pair,require_budget=decision
        if len(self._live_role_rows(side=side))>=self.max_slots:self.veto['SIDE_SLOT_CAP_FULL']+=1;return
        cand=self._candidate_from_levels(side,require_pair,require_budget)
        if cand is None:self.role_budget_blocks[role]+=1;return
        p,q,proj=cand
        if role=='SATELLITE_EXPAND':
            risk_cost=max(0.0,self._physical_floor()-self._candidate_alone_floor(side,p,q))
            if self._available_expand_risk_credit()+EPS<risk_cost:
                self.expandCreditBlocks+=1;self.veto['EXPAND_MONETARY_CREDIT_INSUFFICIENT']+=1;return
        self._submit_role(t,side,role,p,q,proj)

    def _repair_bundle_projection(self,exclude_key=None):
        up=float(self.inv['UP']);dn=float(self.inv['DOWN']);cost=float(self.cost)
        for _,key,o,role in self._live_role_rows():
            if key==exclude_key or role not in {'ECONOMIC_CORE','SATELLITE_REPAIR'}:continue
            if self.key_scope_gen.get(key)!=self.scopeGeneration:continue
            rem=self._remaining(key)
            if rem<=EPS:continue
            if str(o['side'])=='UP':up+=rem
            else:dn+=rem
            cost+=rem*float(o['price'])
        return float(min(up,dn)-cost)

    def _risk_contract_if_needed(self,t:int):
        if self.scopeFloorAnchor is None:return
        after=self._repair_bundle_projection()
        if self.repairBundleProjectionMin is None or after<self.repairBundleProjectionMin:self.repairBundleProjectionMin=after
        if after>=float(self.scopeFloorAnchor)-1e-9:return
        best=None
        for sid,key,o,role in self._live_role_rows():
            if role not in {'ECONOMIC_CORE','SATELLITE_REPAIR'} or o.get('cancelRequested') or self.key_scope_gen.get(key)!=self.scopeGeneration:continue
            alt=self._repair_bundle_projection(exclude_key=key);gain=alt-after
            if gain<=EPS:continue
            priority=0 if role=='SATELLITE_REPAIR' else 1
            item=(priority,-gain,sid,key,role)
            if best is None or item<best:best=item
        if best is not None:self._request_cancel(t,int(best[2]),'REPAIR_BUNDLE_SCOPE_CONTRACT')

    def _reanchor_stale(self,t:int):
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if not o or o.get('cancelRequested'):continue
            role=self.key_role.get(key,'UNASSIGNED');side=str(o['side']);p=v2.kprice(o['price'])
            levels=[v2.kprice(x) for x in self._live_price_levels(side)]
            if role=='ECONOMIC_CORE':
                if p in levels and self._pair_ok(side,p):self.corePreservedClocks+=1;continue
                self._request_cancel(t,sid,'CORE_INVALIDATED');continue
            if p not in levels:
                if self._request_cancel(t,sid,'SATELLITE_FRONTIER_REANCHOR'):self.reanchors+=1

    def run_v6(self,winner):
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
        return {
            'submits':int(self.submits),'fillEvents':int(self.fills),'filledQty':float(self.inv['UP']+self.inv['DOWN']),
            'upQty':float(self.inv['UP']),'downQty':float(self.inv['DOWN']),'buyNotional':float(self.cost),'pnlDiagnosticOnly':pnl,'floor':floor,'best':best,
            'maxSimultaneousSlots':int(self.max_simultaneous_slots),'maxSimultaneousDistinctPrices':int(self.max_simultaneous_distinct_prices),'multiDistinctReceipts':int(self.multi_distinct_receipts),
            'roleSubmits':dict(self.role_submits),'roleFills':dict(self.role_fills),'roleFillQty':{k:float(v) for k,v in self.role_fill_qty.items()},
            'roleCancelRequests':dict(self.role_cancel_requests),'roleBudgetBlocks':dict(self.role_budget_blocks),
            'marginalPairCreditByRole':{k:float(v) for k,v in self.marginal_pair_credit.items()},'marginalPairRiskSpendByRole':{k:float(v) for k,v in self.marginal_pair_risk_spend.items()},
            'scopeSide':self.scopeSide,'scopeFloorAnchor':self.scopeFloorAnchor,'scopeGeneration':int(self.scopeGeneration),'scopeBirths':int(self.scopeBirths),'scopeCompletions':int(self.scopeCompletions),'scopeFlips':int(self.scopeFlips),
            'scopeRiskCreditTotal':float(self.scopeRiskCreditTotal),'scopeRiskCreditConsumed':float(self.scopeRiskCreditConsumed),'scopeRiskCreditReserved':float(self._reserved_current_expand_risk()),'totalRepairCreditValue':float(self.totalRepairCreditValue),
            'scopeRepairProgressClocks':int(self.scopeRepairProgressClocks),'totalRepairProgressClocks':int(self.totalRepairProgressClocks),'expandCreditBlocks':int(self.expandCreditBlocks),
            'staleScopeCancelRequests':int(self.staleScopeCancelRequests),'staleScopeWaits':int(self.staleScopeWaits),'scopeBirthOptionBlocks':int(self.scopeBirthOptionBlocks),'corePreservedClocks':int(self.corePreservedClocks),'repairBundleProjectionMin':self.repairBundleProjectionMin,
            'reanchors':int(self.reanchors),'cancelRequests':dict(self.cancel_requests),'vetoCounts':dict(self.veto),'scopeCreditEvents':self.scope_credit_events[:500],'slotHistory':self.slot_history[:1800]
        }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='role_separated_multislot_v6_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            control=v2.TargetGroundedDistinctSlotSim(tape,4)
            try:r0=control.run_v2(cr['winner'])
            finally:control.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':'V2_DISTINCT_MAX4_CONTROL',**r0})
            sim=RealizedCreditMultiSlotSim(tape,4)
            try:r=sim.run_v6(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':'V7_ROLE_SEPARATED_MONETARY_REALIZED_CREDIT_MAX4',**r})
            print(json.dumps({'progress':mid,'controlPnl':r0['pnlDiagnosticOnly'],'candidatePnl':r['pnlDiagnosticOnly'],'controlFloor':r0['floor'],'candidateFloor':r['floor'],'controlFills':r0['fillEvents'],'candidateFills':r['fillEvents'],'maxDistinct':r['maxSimultaneousDistinctPrices'],'roles':r['roleFills'],'scopeGen':r['scopeGeneration'],'repairCredits':r['totalRepairProgressClocks'],'expandCreditBlocks':r['expandCreditBlocks']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell'].startswith('V2_')};n={r['marketId']:r for r in rows if r['cell'].startswith('V7_')}
        cmp=[]
        for mid in mids:
            a0=c[mid];b=n[mid];cmp.append({'marketId':mid,'pnlDelta':b['pnlDiagnosticOnly']-a0['pnlDiagnosticOnly'],'floorDelta':b['floor']-a0['floor'],'fillDelta':b['fillEvents']-a0['fillEvents'],'submitsDelta':b['submits']-a0['submits']})
        out={'version':'ETH_ROLE_SEPARATED_DISTINCT_MULTISLOT_V7_MONETARY_CREDIT_SMOKE','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'allMarketsCompleted':len(rows)==2*len(mids),'candidateHasCore':all(n[mid]['roleSubmits'].get('ECONOMIC_CORE',0)>0 for mid in mids),'candidateNoIdenticalPriceClaim':'same-side admission excludes occupied live prices'},
             'boundary':['V2 distinct current-live-book price generation frozen','pending Repair provides zero expansion credit','scope-birth realized risk-spend credit plus exact realized Floor-improvement credit from confirmed Repair progress','SATELLITE_EXPAND reserves exact worst-case monetary risk until terminal; each confirmed fill consumes exact filled notional risk','Repair/Core candidate-alone must improve current realized Floor; no assumed future passive fills','FIFO unmatched debt side is scope identity','max4 experimental ceiling only','250ms entry/response, risk queue, <=180s no new exposure','winner post-hoc only','no Target runtime input','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'comparison':cmp,'candidateSummary':[{'marketId':r['marketId'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'fills':r['fillEvents'],'submits':r['submits'],'maxDistinct':r['maxSimultaneousDistinctPrices'],'roles':r['roleFills'],'scopeGen':r['scopeGeneration'],'repairCredits':r['totalRepairProgressClocks']} for r in rows if r['cell'].startswith('V7_')]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
