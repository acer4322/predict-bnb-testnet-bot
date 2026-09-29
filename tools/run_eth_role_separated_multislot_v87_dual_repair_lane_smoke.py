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

class DualRepairLaneSim(v82.SafeParallelCapacitySim):
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots);self.keyRepairLane={};self.pendingRepairLane=None;self.repairLaneSubmits=Counter();self.repairLaneFills=Counter();self.dualLaneBlocks=0
    def _live_repair_lanes(self):
        out=set()
        for _,key,o,role in self._live_role_rows(role='SATELLITE_REPAIR'):
            if self.key_scope_gen.get(key)==self.scopeGeneration:out.add(self.keyRepairLane.get(key,'EXECUTION'))
        return out
    def _pair_aware_candidate(self,side):
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
        lanes=self._live_repair_lanes()
        if int(self.scopeRepairProgressClocks)<=0:
            self.pendingRepairLane='EXECUTION';return super()._candidate_from_levels_v8(side,role,require_pair)
        if 'EXECUTION' not in lanes:
            self.pendingRepairLane='EXECUTION';return super()._candidate_from_levels_v8(side,role,require_pair)
        if 'ECONOMIC' not in lanes:
            self.pendingRepairLane='ECONOMIC';return self._pair_aware_candidate(side)
        self.dualLaneBlocks+=1;self.pendingRepairLane=None;return None
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before=self.n;lane=self.pendingRepairLane if role=='SATELLITE_REPAIR' else None
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='SATELLITE_REPAIR':
            key=f'{side}_{before}';lane=lane or 'EXECUTION';self.keyRepairLane[key]=lane;self.repairLaneSubmits[lane]+=1
            self.slot_history.append({'t':int(t),'event':'REPAIR_LANE_SUBMIT','key':key,'lane':lane,'side':side,'price':float(p),'scopeGeneration':self.key_scope_gen.get(key)})
        self.pendingRepairLane=None;return ok
    def process(self,t):
        before={k:float(o.get('cum') or 0.0) for k,o in self.orders.items()};super().process(t)
        for key,o in self.orders.items():
            inc=float(o.get('cum') or 0.0)-float(before.get(key,0.0))
            if inc>EPS and self.key_role.get(key)=='SATELLITE_REPAIR':self.repairLaneFills[self.keyRepairLane.get(key,'EXECUTION')]+=1
    def run_v87(self,winner):
        r=super().run_v82(winner);r['repairLaneSubmits']=dict(self.repairLaneSubmits);r['repairLaneFills']=dict(self.repairLaneFills);r['dualLaneBlocks']=int(self.dualLaneBlocks);return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='v87_dual_lane_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=v82.SafeParallelCapacitySim(tape,4)
            try:r0=ctl.run_v82(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'V82_SAFE_PARALLEL_CONTROL','winnerPostHocOnly':cr['winner'],**r0});sim=DualRepairLaneSim(tape,4)
            try:r=sim.run_v87(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'V87_DUAL_REPAIR_LANE','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'v82Pnl':r0['pnlDiagnosticOnly'],'v87Pnl':r['pnlDiagnosticOnly'],'v82Floor':r0['floor'],'v87Floor':r['floor'],'v82Fills':r0['fillEvents'],'v87Fills':r['fillEvents'],'v87Submits':r['submits'],'laneFills':r['repairLaneFills'],'laneSubmits':r['repairLaneSubmits'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell'].startswith('V82_')};n={r['marketId']:r for r in rows if r['cell'].startswith('V87_')};cmp=[]
        for m in mids:cmp.append({'marketId':m,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids);livepass=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'ETH_ROLE_SEPARATED_DISTINCT_MULTISLOT_V87_DUAL_REPAIR_LANE_SMOKE','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessSplitPass':correct,'fillRetention50pctResearchGate':livepass},'boundary':['V8.2 safety/correctness frozen','after confirmed Repair progress keep one execution-priority SATELLITE_REPAIR and one pair-aware ECONOMIC SATELLITE_REPAIR concurrently','both share authoritative Repair quota','no new hard safety','max4 distinct live prices','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp,'summary':[{'marketId':m,'pnl':n[m]['pnlDiagnosticOnly'],'floor':n[m]['floor'],'fills':n[m]['fillEvents'],'submits':n[m]['submits'],'laneFills':n[m]['repairLaneFills']} for m in mids]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
