from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
    _spec=importlib.util.spec_from_file_location('frozen_ms4_r1',_STAGED)
    ms4=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(ms4)
else:
    import tools.run_eth_ms4_r1_queue_aware_repair as ms4
v82=ms4.v82;v2=v82.v2;EPS=1e-9

class ExecutionProtectedResidualFanoutSim(ms4.QueueAwareRepairRoutingSim):
    """MS4-R1.1: execution-protected residual-debt Repair fanout.

    The economic Repair satellite is an additional distinct-price option, not a replacement
    for execution-priority Repair. It may reserve only residual authoritative Repair debt after
    existing Repair reservations and must be pure Repair (no overflow). If no execution Repair
    carrier remains, economic satellites are preempted/cancelled so they cannot monopolize the
    last Repair quota. No correctness or risk threshold is relaxed.
    """
    def __init__(self,tape,max_slots:int=4):
        super().__init__(tape,max_slots)
        self.keyRepairLane={}
        self.pendingRepairLane=None
        self.repairLaneSubmits=Counter();self.repairLaneFills=Counter();self.fanoutBlocks=Counter()
        self.economicPreemptCancels=0

    def _live_repair_lane_rows(self,lane=None):
        out=[]
        for sid,key,o,role in self._live_role_rows(role='SATELLITE_REPAIR'):
            if self.key_scope_gen.get(key)!=self.scopeGeneration:continue
            ln=self.keyRepairLane.get(key,'EXECUTION')
            if lane is None or ln==lane:out.append((sid,key,o,role))
        return out

    def _pure_pair_candidate(self,side):
        used=self._used_prices(side)
        levels=[float(v2.kprice(p)) for p in self._live_price_levels(side) if v2.kprice(p) not in used]
        if not levels:return None
        opp='DOWN' if side=='UP' else 'UP';avg=self.unmatched_avg(opp)
        if avg is None:return None
        compatible=sorted([p for p in levels if float(avg)+p<=1.0+EPS],reverse=True)
        for p in compatible:
            q=1.0/p;sp=self._repair_split(side,p,q)
            if sp is None:continue
            if float(sp.get('overflowQty') or 0.0)>EPS:
                self.fanoutBlocks['ECONOMIC_SATELLITE_REQUIRES_PURE_REPAIR']+=1
                continue
            return float(p),float(q),sp['fullFloor'],sp
        self.fanoutBlocks['NO_PURE_PAIR_COMPATIBLE_RESIDUAL_OPTION']+=1
        return None

    def _candidate_from_levels_v8(self,side,role,require_pair):
        if role!='SATELLITE_REPAIR':
            return ms4.QueueAwareRepairRoutingSim._candidate_from_levels_v8(self,side,role,require_pair)

        exec_rows=self._live_repair_lane_rows('EXECUTION')
        econ_rows=self._live_repair_lane_rows('ECONOMIC')

        # Never allow the economic lane to replace execution capacity.
        if not exec_rows:
            self.pendingRepairLane='EXECUTION'
            return v82.SafeParallelCapacitySim._candidate_from_levels_v8(self,side,role,require_pair)

        # Once Repair has physically progressed, add one pure economic satellite only from
        # authoritative residual debt. _repair_split already subtracts all existing reservations.
        if int(self.scopeRepairProgressClocks)>0 and not econ_rows:
            cand=self._pure_pair_candidate(side)
            if cand is not None:
                self.pendingRepairLane='ECONOMIC'
                return cand

        # Economic option is additive. Remaining legal capacity stays execution-priority.
        self.pendingRepairLane='EXECUTION'
        return v82.SafeParallelCapacitySim._candidate_from_levels_v8(self,side,role,require_pair)

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before=self.n;lane=self.pendingRepairLane if role=='SATELLITE_REPAIR' else None
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='SATELLITE_REPAIR':
            key=f'{side}_{before}';lane=lane or 'EXECUTION';self.keyRepairLane[key]=lane;self.repairLaneSubmits[lane]+=1
            self.slot_history.append({'t':int(t),'event':'MS4_REPAIR_LANE_SUBMIT','key':key,'lane':lane,'side':side,'price':float(p),'scopeGeneration':self.key_scope_gen.get(key)})
        self.pendingRepairLane=None
        return ok

    def _preempt_economic_if_execution_missing(self,t:int):
        if self.scopeSide is None:return
        econ=self._live_repair_lane_rows('ECONOMIC')
        if not econ:return
        if self._live_repair_lane_rows('EXECUTION'):return
        for sid,key,o,role in econ:
            if o.get('cancelRequested'):continue
            if self._request_cancel(t,int(sid),'ECONOMIC_PREEMPT_FOR_EXECUTION'):
                self.economicPreemptCancels+=1

    def _open_one_option(self,t:int,qv,end:int):
        self._preempt_economic_if_execution_missing(t)
        return super()._open_one_option(t,qv,end)

    def process(self,t):
        before={k:float(o.get('cum') or 0.0) for k,o in self.orders.items()}
        super().process(t)
        for key,o in self.orders.items():
            inc=float(o.get('cum') or 0.0)-float(before.get(key,0.0))
            if inc>EPS and self.key_role.get(key)=='SATELLITE_REPAIR':
                self.repairLaneFills[self.keyRepairLane.get(key,'EXECUTION')]+=1

    def run_ms4_r1_1(self,winner):
        r=super().run_v88(winner)
        r['repairLaneSubmits']=dict(self.repairLaneSubmits)
        r['repairLaneFills']=dict(self.repairLaneFills)
        r['fanoutBlocks']=dict(self.fanoutBlocks)
        r['economicPreemptCancels']=int(self.economicPreemptCancels)
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r1_1_residual_fanout_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=ms4.QueueAwareRepairRoutingSim(tape,4)
            try:r0=ctl.run_v88(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0})
            sim=ExecutionProtectedResidualFanoutSim(tape,4)
            try:r=sim.run_ms4_r1_1(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_1_EXECUTION_PROTECTED_RESIDUAL_FANOUT','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'r1Pnl':r0['pnlDiagnosticOnly'],'r11Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'r11Floor':r['floor'],'r1Fills':r0['fillEvents'],'r11Fills':r['fillEvents'],'r11Submits':r['submits'],'laneSubmits':r['repairLaneSubmits'],'laneFills':r['repairLaneFills'],'preempt':r['economicPreemptCancels'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell'].startswith('MS4_R1_1_')};cmp=[]
        for m in mids:
            cmp.append({'marketId':m,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids)
        livepass=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'MS4_R1_1_EXECUTION_PROTECTED_RESIDUAL_FANOUT','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':correct,'fillRetention50pctResearchGate':livepass},'boundary':['MS4-R1 correctness/risk kernel frozen','execution-priority Repair is never replaced by economic satellite','one pure pair-compatible economic Repair satellite may use only residual authoritative debt after existing Repair reservations','economic satellite preempted when no execution Repair carrier remains','economic satellite carries no overflow','no new hard safety threshold','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp,'summary':[{'marketId':m,'pnl':n[m]['pnlDiagnosticOnly'],'floor':n[m]['floor'],'fills':n[m]['fillEvents'],'submits':n[m]['submits'],'laneSubmits':n[m]['repairLaneSubmits'],'laneFills':n[m]['repairLaneFills']} for m in mids]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
