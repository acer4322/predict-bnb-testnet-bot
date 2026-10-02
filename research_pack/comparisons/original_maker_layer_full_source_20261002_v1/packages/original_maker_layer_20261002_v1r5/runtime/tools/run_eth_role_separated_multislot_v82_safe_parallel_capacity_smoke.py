from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED_V8=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_role_separated_multislot_v8_repair_overflow_split_smoke.py'
if _STAGED_V8.exists():
    _spec=importlib.util.spec_from_file_location('frozen_v8',_STAGED_V8)
    v8=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(v8)
else:
    import tools.run_eth_role_separated_multislot_v8_repair_overflow_split_smoke as v8
v7=v8.v7;v2=v8.v2;EPS=1e-9

class SafeParallelCapacitySim(v8.RepairOverflowSplitSim):
    """V8.2: preserve V8 correctness while restoring legal multi-slot utilization.

    - An overflow-bearing Repair carrier is exclusive versus other live Repair reservations.
    - If Repair debt is already fully reserved (or no legal extra Repair option exists), unused
      slots may host SATELLITE_EXPAND only from already-realized monetary credit.
    - No safety threshold is relaxed; this is router/capacity utilization only.
    """
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots)
        self.parallelExpandFallbackSubmits=0
        self.parallelExpandFallbackBlocks=0

    def _repair_split(self,side,p,q):
        sp=super()._repair_split(side,p,q)
        if sp is None:return None
        if float(sp['overflowQty'])>EPS and float(sp['reservedRepairBefore'])>EPS:
            self.splitBlocks['OVERFLOW_REPAIR_WAITS_OTHER_REPAIR_TERMINAL']+=1
            return None
        return sp

    def _try_parallel_expand(self,t:int,side:str):
        if self.scopeSide is None or side!=self.scopeSide:return False
        if self._available_expand_risk_credit()<=EPS:
            self.parallelExpandFallbackBlocks+=1;return False
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:
            self.parallelExpandFallbackBlocks+=1;return False
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:
            self.parallelExpandFallbackBlocks+=1;return False
        p,q,proj,split=cand
        risk_cost=max(0.0,self._physical_floor()-self._candidate_alone_floor(side,p,q))
        if self._available_expand_risk_credit()+EPS<risk_cost:
            self.veto['EXPAND_MONETARY_CREDIT_INSUFFICIENT']+=1;self.parallelExpandFallbackBlocks+=1;return False
        if self._submit_role_v8(t,side,'SATELLITE_EXPAND',p,q,proj,None):
            self.parallelExpandFallbackSubmits+=1
            self.slot_history.append({'t':int(t),'event':'PARALLEL_EXPAND_FALLBACK_SUBMIT','side':side,'price':float(p),'riskCost':float(risk_cost),'availableCreditAfter':self._available_expand_risk_credit()})
            return True
        return False

    def _open_one_option(self,t:int,qv,end:int):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:self.veto['LATE_180S']+=1;return
        if self._has_stale_scope_reservation():self.staleScopeWaits+=1;self.veto['STALE_SCOPE_RESERVATION_WAIT']+=1;return
        decision=self._role_decision(qv)
        if decision is None:
            # Parent may WAIT because signal asks for Repair while debt is already fully reserved.
            if self.scopeSide is not None and self._reserved_repair_quota()>=self._scope_debt_qty()-EPS:
                if self._try_parallel_expand(t,self.scopeSide):return
            self.veto['ROLE_WAIT']+=1;return
        side,role,require_pair,require_budget=decision
        if len(self._live_role_rows(side=side))>=self.max_slots:self.veto['SIDE_SLOT_CAP_FULL']+=1;return
        cand=self._candidate_from_levels_v8(side,role,require_pair)
        if cand is None:
            self.role_budget_blocks[role]+=1
            # Only after CORE exists: a blocked additional Repair option may yield the unused slot
            # to an already-funded Expand option. No pending Repair is counted as funding.
            if role=='SATELLITE_REPAIR' and self._core_for_side(side) is not None:
                self._try_parallel_expand(t,self.scopeSide)
            return
        p,q,proj,split=cand
        if role=='SATELLITE_EXPAND':
            risk_cost=max(0.0,self._physical_floor()-self._candidate_alone_floor(side,p,q))
            if self._available_expand_risk_credit()+EPS<risk_cost:
                self.expandCreditBlocks+=1;self.veto['EXPAND_MONETARY_CREDIT_INSUFFICIENT']+=1;return
        self._submit_role_v8(t,side,role,p,q,proj,split)

    def run_v82(self,winner):
        r=super().run_v8(winner)
        r['parallelExpandFallbackSubmits']=int(self.parallelExpandFallbackSubmits)
        r['parallelExpandFallbackBlocks']=int(self.parallelExpandFallbackBlocks)
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='role_multislot_v82_'))
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
            sim=SafeParallelCapacitySim(tape,4)
            try:r=sim.run_v82(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'V82_SAFE_PARALLEL_CAPACITY','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'v7Pnl':r7['pnlDiagnosticOnly'],'v82Pnl':r['pnlDiagnosticOnly'],'v7Floor':r7['floor'],'v82Floor':r['floor'],
                              'v7Fills':r7['fillEvents'],'v82Fills':r['fillEvents'],'v82Submits':r['submits'],'maxDistinct':r['maxSimultaneousDistinctPrices'],
                              'fallback':r['parallelExpandFallbackSubmits'],'unauthOverflow':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell'].startswith('V7_')};n={r['marketId']:r for r in rows if r['cell'].startswith('V82_')}
        cmp=[]
        for mid in mids:
            a0=c[mid];b=n[mid];cmp.append({'marketId':mid,'pnlDelta':b['pnlDiagnosticOnly']-a0['pnlDiagnosticOnly'],'floorDelta':b['floor']-a0['floor'],
                'fillRetention':b['fillEvents']/a0['fillEvents'] if a0['fillEvents'] else None,'submitRetention':b['submits']/a0['submits'] if a0['submits'] else None})
        cont=[m for m in (1945898,1946317) if m in n]
        livepass=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in cont if c[m]['fillEvents']>0)
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids)
        out={'version':'ETH_ROLE_SEPARATED_DISTINCT_MULTISLOT_V82_SAFE_PARALLEL_CAPACITY_SMOKE','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,
             'markets':mids,'rows':rows,'comparison':cmp,'gates':{'allMarketsCompleted':len(rows)==2*len(mids),'correctnessSplitPass':correct,
             'continuationLivenessRetention50pctResearchGate':livepass,'note50pct':'research anti-regression only; not runtime threshold'},
             'boundary':['V8 split correctness preserved','overflow-bearing Repair exclusive while other Repair reservation live','unused slot may route to Expand only from already-realized monetary credit','no pending Repair credit','no safety threshold relaxed','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp,'candidateSummary':[{'marketId':m,'pnl':n[m]['pnlDiagnosticOnly'],'floor':n[m]['floor'],'fills':n[m]['fillEvents'],'submits':n[m]['submits'],'maxDistinct':n[m]['maxSimultaneousDistinctPrices'],'fallback':n[m]['parallelExpandFallbackSubmits']} for m in mids]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
