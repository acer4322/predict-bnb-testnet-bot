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

class NoPreRepairBirthExpandSim(ms4.QueueAwareRepairRoutingSim):
    """Diagnostic counterfactual only: block direct SATELLITE_EXPAND before the first confirmed Repair progress clock.
    All Repair/Overflow split, monetary credit, routing, and physical execution semantics remain unchanged.
    This is NOT a promotion candidate.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots);self.birthExpandBlocks=0
    def _role_decision(self,qv):
        d=super()._role_decision(qv)
        if d is not None and len(d)>=2 and d[1]=='SATELLITE_EXPAND' and int(self.scopeRepairProgressClocks)<=0:
            self.birthExpandBlocks+=1;return None
        return d
    def _try_parallel_expand(self,t,side):
        if int(self.scopeRepairProgressClocks)<=0:
            self.birthExpandBlocks+=1;return False
        return super()._try_parallel_expand(t,side)
    def run_diag(self,winner):
        r=super().run_v88(winner);r['birthExpandBlocks']=int(self.birthExpandBlocks);return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_d1_birth_expand_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=ms4.QueueAwareRepairRoutingSim(tape,4)
            try:r0=ctl.run_v88(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0})
            sim=NoPreRepairBirthExpandSim(tape,4)
            try:r=sim.run_diag(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'MS4_D1_NO_PRE_REPAIR_BIRTH_EXPAND','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'r1Pnl':r0['pnlDiagnosticOnly'],'d1Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d1Floor':r['floor'],'r1Fills':r0['fillEvents'],'d1Fills':r['fillEvents'],'birthBlocks':r['birthExpandBlocks']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell'].startswith('MS4_D1_')};cmp=[]
        for m in mids:cmp.append({'marketId':m,'pnlDeltaNoBirthExpand':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDeltaNoBirthExpand':n[m]['floor']-c[m]['floor'],'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'birthExpandBlocks':n[m]['birthExpandBlocks']})
        out={'version':'MS4_D1_BIRTH_EXPAND_COUNTERFACTUAL','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'diagnosticOnly':True,'markets':mids,'rows':rows,'comparison':cmp,'boundary':['MS4-R1 frozen control','only direct SATELLITE_EXPAND before first confirmed Repair progress is blocked in diagnostic cell','Repair and Repair/Overflow split unchanged','not a promotion candidate','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
