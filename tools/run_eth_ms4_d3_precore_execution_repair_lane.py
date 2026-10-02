from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('ms4r1',_STAGED);r1=importlib.util.module_from_spec(sp);sp.loader.exec_module(r1)
else:
    import tools.run_eth_ms4_r1_queue_aware_repair as r1
EPS=1e-9

class PreCoreExecutionRepairLane(r1.QueueAwareRepairRoutingSim):
    """Diagnostic: preserve Economic Core pair rule but avoid serial stall when Core unavailable.
    While no pair-compatible ECONOMIC_CORE exists, allow one execution-priority pure-Repair
    satellite sharing the same authoritative debt reservation. No overflow is allowed on this lane.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots); self.preCore=Counter(); self.preCoreKeys=set(); self.preCoreFillEvents=0
    def _pure_repair_execution_candidate(self,side):
        used=self._used_prices(side)
        for p in self._live_price_levels(side):
            p=r1.v2.kprice(p)
            if p in used: continue
            q=1.0/p
            sp=self._repair_split(side,p,q)
            if sp is None: continue
            if float(sp.get('overflowQty',0.0))>EPS:
                self.preCore['OVERFLOW_NOT_ALLOWED']+=1; continue
            return float(p),float(q),sp['fullFloor'],sp
        return None
    def _open_one_option(self,t,qv,end):
        if int(end)-int(t)<=r1.v2.NO_NEW_EXPOSURE_MS:self.veto['LATE_180S']+=1;return
        if self._has_stale_scope_reservation():self.staleScopeWaits+=1;self.veto['STALE_SCOPE_RESERVATION_WAIT']+=1;return
        decision=self._role_decision(qv)
        if decision is None:
            if self.scopeSide is not None and self._reserved_repair_quota()>=self._scope_debt_qty()-EPS:
                if self._try_parallel_expand(t,self.scopeSide):return
            self.veto['ROLE_WAIT']+=1;return
        side,role,require_pair,require_budget=decision
        if len(self._live_role_rows(side=side))>=self.max_slots:self.veto['SIDE_SLOT_CAP_FULL']+=1;return
        cand=self._candidate_from_levels_v8(side,role,require_pair)
        if cand is None and role=='ECONOMIC_CORE':
            # Core economics remains intact. Only if Core cannot materialize, permit one pure
            # execution Repair carrier. Existing satellite reservation blocks another until terminal.
            if self._live_role_rows(role='SATELLITE_REPAIR',side=side):
                self.preCore['ONE_PRECORE_EXECUTION_LANE_BUSY']+=1; self.role_budget_blocks[role]+=1; return
            alt=self._pure_repair_execution_candidate(side)
            if alt is not None:
                p,q,proj,split=alt; before=self.n
                if self._submit_role_v8(t,side,'SATELLITE_REPAIR',p,q,proj,split):
                    key=f'{side}_{before}'; self.preCoreKeys.add(key); self.preCore['SUBMIT']+=1
                    self.slot_history.append({'t':int(t),'event':'PRECORE_EXECUTION_REPAIR_SUBMIT','key':key,'side':side,'price':p,'repairQty':split['repairQty'],'source':'CORE_UNAVAILABLE_PURE_REPAIR_EXECUTION_LANE'})
                    return
            self.preCore['NO_LEGAL_PRECORE_EXECUTION_REPAIR']+=1; self.role_budget_blocks[role]+=1; return
        if cand is None:
            self.role_budget_blocks[role]+=1
            if role=='SATELLITE_REPAIR' and self._core_for_side(side) is not None:self._try_parallel_expand(t,self.scopeSide)
            return
        p,q,proj,split=cand
        if role=='SATELLITE_EXPAND':
            risk_cost=max(0.0,self._physical_floor()-self._candidate_alone_floor(side,p,q))
            if self._available_expand_risk_credit()+EPS<risk_cost:self.expandCreditBlocks+=1;self.veto['EXPAND_MONETARY_CREDIT_INSUFFICIENT']+=1;return
        self._submit_role_v8(t,side,role,p,q,proj,split)
    def process(self,t):
        before={k:float(o.get('cum') or 0.0) for k,o in self.orders.items()}; super().process(t)
        for k in self.preCoreKeys:
            o=self.orders.get(k)
            if o is not None and float(o.get('cum') or 0.0)>float(before.get(k,0.0))+EPS:self.preCoreFillEvents+=1
    def run_d3(self,winner):
        r=super().run_v88(winner);r['preCore']=dict(self.preCore);r['preCoreFillEvents']=int(self.preCoreFillEvents);return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_d3_precore_exec_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            c=r1.QueueAwareRepairRoutingSim(tape,4)
            try:r0=c.run_v88(cr['winner'])
            finally:c.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0})
            s=PreCoreExecutionRepairLane(tape,4)
            try:r=s.run_d3(cr['winner'])
            finally:s.close()
            rows.append({'marketId':mid,'cell':'MS4_D3_PRECORE_EXECUTION_REPAIR','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'r1Sub':r0['submits'],'d3Sub':r['submits'],'r1Fill':r0['fillEvents'],'d3Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'d3Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d3Floor':r['floor'],'preCore':r['preCore'],'preCoreFills':r['preCoreFillEvents'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_D3_PRECORE_EXECUTION_REPAIR'}
        cmp=[{'marketId':m,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'preCoreFills':n[m]['preCoreFillEvents']} for m in mids]
        out={'version':'MS4_D3_PRECORE_EXECUTION_REPAIR_LANE_20260905','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapsePass':all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['Economic Core pair<=1 remains unchanged','one pre-Core execution Repair lane only when Core candidate unavailable','pre-Core lane must be pure Repair with zero Overflow','shares authoritative Repair debt reservation','no new hard safety','realistic HFT','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
