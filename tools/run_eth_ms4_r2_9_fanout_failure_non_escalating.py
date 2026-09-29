from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
r1=r28.r1; EPS=1e-9

class FanoutFailureNonEscalatingSim(r28.FanoutRoleCapacitySim):
    """R2.9: fanout is supplemental Passive coverage, not Active-escalation evidence.
    Fanout orders retain normal debt reservation/fill accounting; only their terminal 0-fill
    is excluded from FailureEvidenceActiveDrain evidence. Native Core/Satellite evidence unchanged.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots)
        self.nonEscalatingFanoutKeys=set(); self.r29={'FANOUT_KEYS_MARKED_NON_ESCALATING':0}
    def _parallel_repair_fill(self,t:int,side:str):
        before=set(self.fanoutKeys)
        made=super()._parallel_repair_fill(t,side)
        new=set(self.fanoutKeys)-before
        for k in new:
            # failedSeen is used by R2.2 only to suppress creation of Active failure evidence.
            # It does not release reservation or alter HFT lifecycle handling.
            self.failedSeen.add(k)
            self.nonEscalatingFanoutKeys.add(k)
            self.r29['FANOUT_KEYS_MARKED_NON_ESCALATING']+=1
            self.slot_history.append({'t':int(t),'event':'FANOUT_FAILURE_NON_ESCALATING_MARK','key':k,'generation':int(self.scopeGeneration)})
        return made
    def run_r29(self,w):
        r=super().run_cap(w)
        r['r29Stats']=dict(self.r29); r['nonEscalatingFanoutKeyCount']=len(self.nonEscalatingFanoutKeys)
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r29_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}; rows=[]
        for mid in mids:
            cr=co[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'
            b=r1.QueueAwareRepairRoutingSim(tape,4)
            try:r0=b.run_v88(cr['winner'])
            finally:b.close()
            c=r28.FanoutRoleCapacitySim(tape,1,4)
            try:rc=c.run_cap(cr['winner'])
            finally:c.close()
            s=FanoutFailureNonEscalatingSim(tape,4)
            try:r=s.run_r29(cr['winner'])
            finally:s.close()
            rows += [
                {'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0},
                {'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**rc},
                {'marketId':mid,'cell':'MS4_R29_FANOUT_FAILURE_NON_ESCALATING','winnerPostHocOnly':cr['winner'],**r},
            ]
            print(json.dumps({'marketId':mid,'r1':{'fills':r0['fillEvents'],'pnl':r0['pnlDiagnosticOnly'],'floor':r0['floor']},'cap1':{'fills':rc['fillEvents'],'pnl':rc['pnlDiagnosticOnly'],'floor':rc['floor'],'fan':rc['parallelPassiveFanoutSubmits'],'active':rc.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)},'r29':{'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'fan':r['parallelPassiveFanoutSubmits'],'active':r.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0),'marked':r['nonEscalatingFanoutKeyCount']},'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'}; C={r['marketId']:r for r in rows if r['cell']=='MS4_R29_FANOUT_FAILURE_NON_ESCALATING'}
        cmp=[]
        for m in mids:
            b,c=B[m],C[m]; cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'activeDelta':c.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)-b.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)})
        correctness=all(float(C[m].get('unauthorizedOverflowQty',0) or 0)<=EPS and float(C[m].get('repairQuotaExcessMax',0) or 0)<=EPS for m in mids)
        anti=all(C[m]['fillEvents']>=.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        out={'version':'MS4_R2_9_FANOUT_FAILURE_NON_ESCALATING_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correctness,'antiCollapse50pctPass':anti},'boundary':['max_slots remains 4','R2.8 CAP1 role occupancy unchanged','fanout orders retain normal Repair reservation/fill accounting','only fanout terminal zero-fill is prevented from creating Active escalation evidence','native Core/native Satellite failure evidence unchanged','Overflow/debt/Active qty authority unchanged','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
