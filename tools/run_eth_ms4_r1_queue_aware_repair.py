from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_role_separated_multislot_v82_safe_parallel_capacity_smoke.py'
if _STAGED.exists():
    _spec=importlib.util.spec_from_file_location('frozen_v82',_STAGED);v82=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(v82)
else:
    import tools.run_eth_role_separated_multislot_v82_safe_parallel_capacity_smoke as v82
v8=v82.v8;v7=v82.v7;v2=v82.v2;EPS=1e-9

class QueueAwareRepairRoutingSim(v82.SafeParallelCapacitySim):
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots);self.queueRouting=Counter();self.queueDecisions=[]
    def _level_depth(self,side,p):
        if side=='UP':return float(self.book['bids'].get(float(p),float('inf')))
        ask=round(1.0-float(p),10)
        # tolerate floating key representation from tape
        if ask in self.book['asks']:return float(self.book['asks'][ask])
        vals=[(abs(float(k)-ask),float(v)) for k,v in self.book['asks'].items()]
        return min(vals)[1] if vals else float('inf')
    def _pair_candidate(self,side):
        used=self._used_prices(side);levels=[float(v2.kprice(p)) for p in self._live_price_levels(side) if v2.kprice(p) not in used]
        if not levels:return None
        opp='DOWN' if side=='UP' else 'UP';avg=self.unmatched_avg(opp)
        if avg is None:return None
        compatible=sorted([p for p in levels if float(avg)+p<=1.0+EPS],reverse=True)
        incompatible=sorted([p for p in levels if p not in compatible])
        for p in compatible+incompatible:
            q=1.0/p;sp=self._repair_split(side,p,q)
            if sp is None:continue
            return float(p),float(q),sp['fullFloor'],sp
        return None
    def _candidate_from_levels_v8(self,side,role,require_pair):
        if role!='SATELLITE_REPAIR':return super()._candidate_from_levels_v8(side,role,require_pair)
        exec_cand=super()._candidate_from_levels_v8(side,role,require_pair)
        if int(self.scopeRepairProgressClocks)<=0 or exec_cand is None:
            self.queueRouting['PRE_PROGRESS_EXECUTION']+=1;return exec_cand
        pair_cand=self._pair_candidate(side)
        if pair_cand is None:
            self.queueRouting['NO_PAIR_CANDIDATE_EXECUTION']+=1;return exec_cand
        ep=float(exec_cand[0]);pp=float(pair_cand[0])
        if abs(ep-pp)<=EPS:
            self.queueRouting['SAME_PRICE']+=1;return exec_cand
        ed=self._level_depth(side,ep);pd=self._level_depth(side,pp)
        choose_pair=pd<=ed
        self.queueRouting['PAIR_QUEUE_NOT_WORSE' if choose_pair else 'EXECUTION_QUEUE_BETTER']+=1
        self.queueDecisions.append({'scopeGeneration':int(self.scopeGeneration),'side':side,'executionPrice':ep,'pairPrice':pp,'executionDepth':ed,'pairDepth':pd,'chosen':'PAIR' if choose_pair else 'EXECUTION'})
        return pair_cand if choose_pair else exec_cand
    def run_v88(self,winner):
        r=super().run_v82(winner);r['queueRouting']=dict(self.queueRouting);r['queueDecisions']=self.queueDecisions[:500];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='v88_queue_route_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=v82.SafeParallelCapacitySim(tape,4)
            try:r0=ctl.run_v82(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'MS4_R0_SAFE_PARALLEL_CONTROL','winnerPostHocOnly':cr['winner'],**r0});sim=QueueAwareRepairRoutingSim(tape,4)
            try:r=sim.run_v88(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_QUEUE_AWARE_REPAIR_ROUTING','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'v82Pnl':r0['pnlDiagnosticOnly'],'v88Pnl':r['pnlDiagnosticOnly'],'v82Floor':r0['floor'],'v88Floor':r['floor'],'v82Fills':r0['fillEvents'],'v88Fills':r['fillEvents'],'v88Submits':r['submits'],'routing':r['queueRouting'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell'].startswith('MS4_R0_')};n={r['marketId']:r for r in rows if r['cell'].startswith('MS4_R1_')};cmp=[]
        for m in mids:cmp.append({'marketId':m,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids);livepass=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'ETH_ROLE_SEPARATED_DISTINCT_MULTISLOT_MS4_R1_QUEUE_AWARE_REPAIR_ROUTING_SMOKE','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessSplitPass':correct,'fillRetention50pctResearchGate':livepass},'boundary':['V8.2 safety/correctness frozen','after first confirmed Repair progress compare execution-priority vs pair-aware legal Repair candidates','choose pair-aware only when displayed level depth <= execution candidate depth','no fixed queue threshold','no hard safety added','realistic HFT','risk queue model','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp,'summary':[{'marketId':m,'pnl':n[m]['pnlDiagnosticOnly'],'floor':n[m]['floor'],'fills':n[m]['fillEvents'],'submits':n[m]['submits'],'routing':n[m]['queueRouting']} for m in mids]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
