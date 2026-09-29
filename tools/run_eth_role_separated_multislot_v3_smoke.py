from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter,defaultdict

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
HBT244=Path(__file__).resolve().parent/'hftbacktest_244'
if HBT244.exists() and str(HBT244) not in sys.path: sys.path.insert(0,str(HBT244))

import importlib.util
_STAGED_V2=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_target_grounded_distinct_multislot_v2_smoke.py'
if _STAGED_V2.exists():
    _spec=importlib.util.spec_from_file_location('frozen_distinct_v2',_STAGED_V2);v2=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(v2)
else:
    import tools.run_eth_target_grounded_distinct_multislot_v2_smoke as v2

EPS=1e-9

class RoleSeparatedMultiSlotSim(v2.TargetGroundedDistinctSlotSim):
    """Role-separated distinct-price multi-slot candidate.

    Architecture under test:
    - ECONOMIC_CORE owns the cheapest admissible responsibility path and keeps queue.
    - SATELLITE_REPAIR / SATELLITE_EXPAND may use other live distinct prices.
    - CORE admission keeps exact Pair economics.
    - SATELLITE admission spends only from exact joint all-live/pending Scoped-Floor budget.
    - Before a two-sided physical base exists, repeated same-side exposure is only legal while
      an opposite ECONOMIC_CORE is already reserved and the all-fill bundle is non-worsening.
    - No fixed Target tick spacing, slot count claim, PnL threshold, or future information.
    """
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots)
        self.key_role:dict[str,str]={}
        self.role_submits=Counter();self.role_fills=Counter();self.role_fill_qty=defaultdict(float)
        self.role_cancel_requests=Counter();self.role_budget_blocks=Counter();self.core_preserved_clocks=0
        self.shared_budget_min=None;self.marginal_pair_risk_spend=defaultdict(float);self.marginal_pair_credit=defaultdict(float)
        self.prebase_same_side_satellite_submits=0;self.prebase_core_submits=0

    def process(self,t):
        before={key:float(o.get('cum') or 0.0) for key,o in self.orders.items()}
        super().process(t)
        for key,o in self.orders.items():
            inc=float(o.get('cum') or 0.0)-float(before.get(key,0.0))
            if inc>EPS:
                role=self.key_role.get(key,'UNASSIGNED')
                self.role_fills[role]+=1;self.role_fill_qty[role]+=inc
                self.slot_history.append({'t':int(t),'event':'ROLE_FILL','key':key,'role':role,'side':o['side'],'price':float(o['price']),'fillInc':inc,'cum':float(o['cum'])})

    def _request_cancel(self,t:int,sid:int,reason:str)->bool:
        key=self.slot_key.get(int(sid));role=self.key_role.get(key,'UNASSIGNED') if key else 'UNASSIGNED'
        ok=super()._request_cancel(t,sid,reason)
        if ok:self.role_cancel_requests[f'{role}:{reason}']+=1
        return ok

    def _state(self):
        u=float(self.inv['UP']);d=float(self.inv['DOWN'])
        if u<=EPS and d<=EPS:return ('EMPTY',None,None)
        if u>EPS and d<=EPS:return ('ONE_SIDED','UP','DOWN')
        if d>EPS and u<=EPS:return ('ONE_SIDED','DOWN','UP')
        return ('TWO_SIDED',None,self._weak_side())

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

    def _physical_floor(self):
        return float(min(self.inv['UP'],self.inv['DOWN'])-self.cost)

    def _shared_budget_ok(self,side,p,q):
        # Empty portfolio gets exactly one initial asymmetric Probe opportunity.
        if float(self.inv['UP'])<=EPS and float(self.inv['DOWN'])<=EPS:
            return True,None
        before=self._physical_floor()
        after,_,_,_=self._pending_projection((side,float(p),float(q)))
        if self.shared_budget_min is None or after<self.shared_budget_min:self.shared_budget_min=after
        return after>=before-1e-9,after

    def _pair_edge(self,side,p,q):
        opp='DOWN' if side=='UP' else 'UP'
        oppq=sum(float(a) for a,_ in self.un[opp])
        if oppq<=EPS:return (0.0,0.0)
        avg=self.unmatched_avg(opp)
        if avg is None:return (0.0,0.0)
        edge=1.0-(float(avg)+float(p))
        if edge>=0:return (float(edge)*float(q),0.0)
        return (0.0,float(-edge)*float(q))

    def _used_prices(self,side):
        return {v2.kprice(o['price']) for _,_,o,_ in self._live_role_rows(side=side)}

    def _candidate_from_levels(self,side:str,require_pair:bool,require_budget:bool):
        used=self._used_prices(side)
        for p in self._live_price_levels(side):
            p=v2.kprice(p)
            if p in used:continue
            q=1.0/p
            if require_pair and not self._pair_ok(side,p):
                self.veto['CORE_PAIR_ECONOMICS']+=1;continue
            proj=None
            if require_budget:
                ok,proj=self._shared_budget_ok(side,p,q)
                if not ok:
                    self.veto['SHARED_SCOPED_FLOOR_BUDGET']+=1;continue
            return float(p),float(q),proj
        return None

    def _role_decision(self,qv):
        state,held_or_none,repair_or_weak=self._state()
        signal=self._direction(qv)
        if state=='EMPTY':
            return signal,'PROBE_CORE',False,False
        if state=='ONE_SIDED':
            held=held_or_none;missing=repair_or_weak
            if self._core_for_side(missing) is None:
                # Create a passive repair option, not a forced physical balance fill.
                return missing,'ECONOMIC_CORE',True,True
            # With a repair core already reserved, continuation is allowed only from shared bundle budget.
            if signal==held:
                return held,'SATELLITE_EXPAND',False,True
            return missing,'SATELLITE_REPAIR',False,True
        weak=repair_or_weak
        # Keep at least one economic repair core on current weak side before satellites spend risk.
        if weak is not None and self._core_for_side(weak) is None:
            return weak,'ECONOMIC_CORE',True,True
        if signal==weak:
            return signal,'SATELLITE_REPAIR',False,True
        return signal,'SATELLITE_EXPAND',False,True

    def _submit_role(self,t:int,side:str,role:str,p:float,q:float,proj,source:str):
        if self._last_new_receipt==int(t):
            self.one_new_per_receipt_blocks+=1;self.veto['ONE_NEW_OPTION_PER_RECEIPT']+=1;return False
        if len(self.slot_key)>=self.max_slots:
            self.veto['GLOBAL_SLOT_CAP_FULL']+=1;return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:return False
        before_n=self.n;self.submit(int(t),side,float(p),float(q));key=f'{side}_{before_n}'
        self.slot_key[int(free)]=key;self.key_role[key]=role;self._last_new_receipt=int(t);self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,p,q);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        state,held,_=self._state()
        if state=='ONE_SIDED' and role=='ECONOMIC_CORE':self.prebase_core_submits+=1
        if state=='ONE_SIDED' and role=='SATELLITE_EXPAND' and side==held:self.prebase_same_side_satellite_submits+=1
        self.slot_history.append({'t':int(t),'event':'ROLE_SLOT_SUBMIT','slotId':int(free),'key':key,'role':role,'side':side,'price':float(p),'qty':float(q),'jointProjectedFloor':proj,'physicalFloor':self._physical_floor(),'source':source})
        return True

    def _open_one_option(self,t:int,qv,end:int):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:
            self.veto['LATE_180S']+=1;return
        side,role,require_pair,require_budget=self._role_decision(qv)
        if len(self._live_role_rows(side=side))>=self.max_slots:
            self.veto['SIDE_SLOT_CAP_FULL']+=1;return
        cand=self._candidate_from_levels(side,require_pair,require_budget)
        if cand is None:
            self.role_budget_blocks[role]+=1;return
        p,q,proj=cand
        self._submit_role(t,side,role,p,q,proj,'CURRENT_STRICT_PAST_LIVE_BOOK_ROLE_SEPARATED')

    def _reanchor_stale(self,t:int):
        state,held,weak=self._state()
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if not o or o.get('cancelRequested'):continue
            role=self.key_role.get(key,'UNASSIGNED');side=str(o['side']);p=v2.kprice(o['price'])
            levels=[v2.kprice(x) for x in self._live_price_levels(side)]
            if role=='ECONOMIC_CORE':
                # Core queue is preserved through ordinary frontier motion. Reanchor only when
                # its literal live price vanished or exact Pair economics no longer holds.
                if p in levels and self._pair_ok(side,p):
                    self.core_preserved_clocks+=1;continue
                self._request_cancel(t,sid,'CORE_INVALIDATED')
                continue
            # Satellites are execution options: keep only while their distinct live level remains observable.
            if p not in levels:
                if self._request_cancel(t,sid,'SATELLITE_FRONTIER_REANCHOR'):self.reanchors+=1
        # When slots are scarce, never sacrifice CORE before satellites.
        if len(self.slot_key)>self.max_slots:
            rows=[]
            for sid,key in self.slot_key.items():
                role=self.key_role.get(key,'UNASSIGNED')
                rows.append((0 if role!='ECONOMIC_CORE' else 1,sid,key))
            for _,sid,_ in sorted(rows)[:max(0,len(rows)-self.max_slots)]:self._request_cancel(t,sid,'ROLE_CAPACITY_CONTRACT')

    def _risk_contract_if_needed(self,t:int):
        if float(self.inv['UP'])<=EPS and float(self.inv['DOWN'])<=EPS:return
        before=self._physical_floor();after,_,_,_=self._pending_projection()
        if after>=before-1e-9:return
        # Shared budget contraction: satellites are removed before economic cores.
        best=None
        for sid,key in self.slot_key.items():
            o=self.orders.get(key)
            if not o or o.get('cancelRequested'):continue
            alt,_,_,_=self._pending_projection(exclude_key=key);gain=alt-after
            role=self.key_role.get(key,'UNASSIGNED');priority=0 if role!='ECONOMIC_CORE' else 1
            item=(priority,-gain,sid,key,alt,role)
            if best is None or item<best:best=item
        if best is not None:
            sid=int(best[2]);role=best[5]
            if self._request_cancel(t,sid,'SHARED_BUDGET_CONTRACT'):
                self.veto[f'SHARED_BUDGET_CONTRACT_{role}']+=1

    def run_v3(self,winner):
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
            'upQty':float(self.inv['UP']),'downQty':float(self.inv['DOWN']),'buyNotional':float(self.cost),
            'pnlDiagnosticOnly':pnl,'floor':floor,'best':best,
            'maxSimultaneousSlots':int(self.max_simultaneous_slots),'maxSimultaneousDistinctPrices':int(self.max_simultaneous_distinct_prices),
            'multiDistinctReceipts':int(self.multi_distinct_receipts),'occupancyDistribution':{str(k):int(v) for k,v in sorted(self.occupancy.items())},
            'roleSubmits':dict(self.role_submits),'roleFills':dict(self.role_fills),'roleFillQty':{k:float(v) for k,v in self.role_fill_qty.items()},
            'roleCancelRequests':dict(self.role_cancel_requests),'roleBudgetBlocks':dict(self.role_budget_blocks),
            'marginalPairCreditByRole':{k:float(v) for k,v in self.marginal_pair_credit.items()},
            'marginalPairRiskSpendByRole':{k:float(v) for k,v in self.marginal_pair_risk_spend.items()},
            'prebaseCoreSubmits':int(self.prebase_core_submits),'prebaseSameSideSatelliteSubmits':int(self.prebase_same_side_satellite_submits),
            'corePreservedClocks':int(self.core_preserved_clocks),'sharedBudgetProjectionMin':self.shared_budget_min,
            'reanchors':int(self.reanchors),'cancelRequests':dict(self.cancel_requests),'vetoCounts':dict(self.veto),'slotHistory':self.slot_history[:1600]
        }


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='role_separated_multislot_v3_'))
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
            sim=RoleSeparatedMultiSlotSim(tape,4)
            try:r=sim.run_v3(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':'V3_ROLE_SEPARATED_SHARED_BUDGET_MAX4',**r})
            print(json.dumps({'progress':mid,'controlPnl':r0['pnlDiagnosticOnly'],'candidatePnl':r['pnlDiagnosticOnly'],'controlFloor':r0['floor'],'candidateFloor':r['floor'],'controlFills':r0['fillEvents'],'candidateFills':r['fillEvents'],'maxDistinct':r['maxSimultaneousDistinctPrices'],'roles':r['roleFills'],'veto':r['vetoCounts']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell'].startswith('V2_')};n={r['marketId']:r for r in rows if r['cell'].startswith('V3_')}
        cmp=[]
        for mid in mids:
            a0=c[mid];b=n[mid];cmp.append({'marketId':mid,'pnlDelta':b['pnlDiagnosticOnly']-a0['pnlDiagnosticOnly'],'floorDelta':b['floor']-a0['floor'],'fillDelta':b['fillEvents']-a0['fillEvents'],'submitsDelta':b['submits']-a0['submits']})
        out={'version':'ETH_ROLE_SEPARATED_DISTINCT_MULTISLOT_V3_SMOKE','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'allMarketsCompleted':len(rows)==2*len(mids),'candidateHasCore':all(n[mid]['roleSubmits'].get('ECONOMIC_CORE',0)>0 for mid in mids),'candidateNoIdenticalPriceClaim':'same-side admission excludes occupied live prices'},
             'boundary':['V2 distinct live-price generation unchanged','max4 experimental ceiling only','ECONOMIC_CORE exact Pair economics; satellites consume exact shared all-fill Scoped-Floor budget','core queue preserved across ordinary frontier movement; satellites may reanchor','pre-base same-side continuation requires an already-reserved opposite core plus shared budget','no fixed Target spacing/qty/time threshold added','250ms entry/response, risk queue, <=180s no new exposure','winner post-hoc only','no Target runtime input','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'comparison':cmp,'candidateSummary':[{'marketId':r['marketId'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'fills':r['fillEvents'],'submits':r['submits'],'maxDistinct':r['maxSimultaneousDistinctPrices'],'roles':r['roleFills']} for r in rows if r['cell'].startswith('V3_')]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
