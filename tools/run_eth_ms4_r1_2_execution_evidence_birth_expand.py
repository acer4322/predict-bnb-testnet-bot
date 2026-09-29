from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
    _spec=importlib.util.spec_from_file_location('frozen_ms4_r1',_STAGED);ms4=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(ms4)
else:
    import tools.run_eth_ms4_r1_queue_aware_repair as ms4
EPS=1e-9
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}
LIVE_STATUSES={'NEW','PARTIALLY_FILLED'}
TERMINAL_NOFILL_STATUSES={'CANCELED','CANCELLED','EXPIRED'}

class ExecutionEvidenceBirthExpandSim(ms4.QueueAwareRepairRoutingSim):
    """MS4-R1.2: initial birth-credit Expand becomes execution-evidence fallback.

    Direct SATELLITE_EXPAND before the first confirmed Repair progress is not allowed merely
    because scope birth created monetary credit. It is unlocked when a same-scope Repair carrier
    was actually observed live/working and later terminal with zero fill. This uses order lifecycle
    evidence, not a new clock/seconds threshold. Repair, multi-slot, overflow, and later realized-
    credit Expand semantics remain unchanged.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots)
        self.keySeenLive=set();self.birthExpandExecutionUnlocked=False
        self.repairZeroFillTerminals=0;self.birthExpandGateBlocks=0;self.birthExpandUnlockEvents=[]
        self._gateScopeGeneration=int(self.scopeGeneration)

    def _sync_scope(self,t,new_side,floor_before):
        old_gen=int(self.scopeGeneration);old_side=self.scopeSide
        super()._sync_scope(t,new_side,floor_before)
        if int(self.scopeGeneration)!=old_gen or self.scopeSide!=old_side:
            self.birthExpandExecutionUnlocked=False
            self._gateScopeGeneration=int(self.scopeGeneration)

    def _refresh_slots(self,t:int):
        # Observe native/HFT lifecycle before base removes explicit terminal slots.
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if not o:continue
            try:s=self.snap(o)
            except Exception:s={}
            status=str(s.get('status') or '').upper()
            if status in LIVE_STATUSES:self.keySeenLive.add(key)
            if status in TERMINAL_NOFILL_STATUSES and key in self.keySeenLive:
                role=self.key_role.get(key,'UNASSIGNED');gen=self.key_scope_gen.get(key)
                cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
                if role in REPAIR_ROLES and self.scopeSide is not None and gen==self.scopeGeneration and cum<=EPS:
                    self.repairZeroFillTerminals+=1
                    if not self.birthExpandExecutionUnlocked and int(self.scopeRepairProgressClocks)<=0:
                        self.birthExpandExecutionUnlocked=True
                        ev={'t':int(t),'event':'BIRTH_EXPAND_EXECUTION_EVIDENCE_UNLOCK','generation':int(self.scopeGeneration),'key':key,'role':role,'terminalStatus':status}
                        self.birthExpandUnlockEvents.append(ev);self.slot_history.append(ev)
        return super()._refresh_slots(t)

    def _birth_expand_allowed(self):
        return int(self.scopeRepairProgressClocks)>0 or bool(self.birthExpandExecutionUnlocked)

    def _role_decision(self,qv):
        d=super()._role_decision(qv)
        if d is not None and len(d)>=2 and d[1]=='SATELLITE_EXPAND' and not self._birth_expand_allowed():
            self.birthExpandGateBlocks+=1;return None
        return d

    def _try_parallel_expand(self,t,side):
        if not self._birth_expand_allowed():
            self.birthExpandGateBlocks+=1;return False
        return super()._try_parallel_expand(t,side)

    def run_ms4_r1_2(self,winner):
        r=super().run_v88(winner)
        r['repairZeroFillTerminals']=int(self.repairZeroFillTerminals)
        r['birthExpandGateBlocks']=int(self.birthExpandGateBlocks)
        r['birthExpandExecutionUnlocked']=bool(self.birthExpandExecutionUnlocked)
        r['birthExpandUnlockEvents']=self.birthExpandUnlockEvents[:200]
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r1_2_exec_evidence_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=ms4.QueueAwareRepairRoutingSim(tape,4)
            try:r0=ctl.run_v88(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0})
            sim=ExecutionEvidenceBirthExpandSim(tape,4)
            try:r=sim.run_ms4_r1_2(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_2_EXECUTION_EVIDENCE_BIRTH_EXPAND','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'r1Pnl':r0['pnlDiagnosticOnly'],'r12Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'r12Floor':r['floor'],'r1Fills':r0['fillEvents'],'r12Fills':r['fillEvents'],'r12Submits':r['submits'],'zeroFillRepairTerminals':r['repairZeroFillTerminals'],'unlocks':len(r['birthExpandUnlockEvents']),'gateBlocks':r['birthExpandGateBlocks'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell'].startswith('MS4_R1_2_')};cmp=[]
        for m in mids:cmp.append({'marketId':m,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'unlocks':len(n[m]['birthExpandUnlockEvents'])})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids)
        livepass=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'MS4_R1_2_EXECUTION_EVIDENCE_BIRTH_EXPAND','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':correct,'fillRetention50pctResearchGate':livepass},'boundary':['MS4-R1 frozen except initial birth-credit direct Expand authority','before first confirmed Repair progress direct Expand requires a same-scope Repair carrier observed live then terminal zero-fill','no fixed seconds/count threshold','Repair and multi-slot execution continue while gate closed','confirmed Repair progress restores ordinary MS4-R1 Expand behavior','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
