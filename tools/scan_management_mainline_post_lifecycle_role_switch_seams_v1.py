from __future__ import annotations
import argparse,json,tempfile,zipfile,os
from pathlib import Path
import importlib.util
try:
    from tools import run_management_mainline_v3b_closed_loop_role_manager_v1 as cl
except ImportError:
    _p=Path(__file__).with_name('run_management_mainline_v3b_closed_loop_role_manager_v1.py')
    _sp=importlib.util.spec_from_file_location('run_management_mainline_v3b_closed_loop_role_manager_v1',_p); cl=importlib.util.module_from_spec(_sp); _sp.loader.exec_module(cl)

REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}
EXPAND_ROLES={'PROBE_CORE','SATELLITE_EXPAND'}

class Scanner(cl.ClosedLoopManager):
    def __init__(self,tape,mid,boundary_t,boundary_reason):
        super().__init__(tape,'NATIVE')
        self.mid=int(mid);self.boundary_t=int(boundary_t);self.boundary_reason=str(boundary_reason);self.hit=None
    def _capture_pre(self,t,e,qv):
        return {
            'marketId':self.mid,'t':int(t),'weakSide':str(e['weakSide']),'expandSide':str(e['expandSide']),
            'repairCandidate':e['repairCandidate'],'expandCandidate':e['expandCandidate'],'freeSlots':int(e['freeSlots']),
            'repairProgressFrac':float(e['repairProgressFrac']),'remainingDebtQty':float(e['remainingDebtQty']),
            'inventory':{'UP':float(self.inv['UP']),'DOWN':float(self.inv['DOWN'])},'cost':float(self.cost),
            'floor':float(min(float(self.inv['UP'])-float(self.cost),float(self.inv['DOWN'])-float(self.cost))),'best':float(max(float(self.inv['UP'])-float(self.cost),float(self.inv['DOWN'])-float(self.cost))),'liveSlots':len(self.slot_key),
            'qLadderLive':self.q_ladder is not None,'qPendingActive':self.q_pending_active is not None,
            'sourceBoundaryT':self.boundary_t,'sourceBoundaryReason':self.boundary_reason,
            'decisionLagAfterBoundaryMsDiagnostic':int(t)-self.boundary_t,
        }
    def _open_one_option(self,t,qv,end):
        if self.hit is not None or int(t)<self.boundary_t:
            return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        e=self._eligible(t,qv,end)
        if e is None:
            return cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        pre=self._capture_pre(t,e,qv); before_n=int(self.n); before_sub=int(self.submits)
        out=cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
        new=[]
        for n in range(before_n,int(self.n)):
            for side in ('UP','DOWN'):
                k=f'{side}_{n}'
                if k in self.orders:
                    role=str(self.key_role.get(k,'UNASSIGNED'))
                    if k in getattr(self,'activeKeys',set()): continue
                    if role in REPAIR_ROLES|EXPAND_ROLES:
                        new.append((k,side,role))
        if int(self.submits)>before_sub and len(new)==1:
            k,side,role=new[0]
            pre['nativeKey']=k;pre['nativeSide']=side;pre['nativeRole']=role
            pre['nativeClass']='REPAIR' if role in REPAIR_ROLES else 'EXPAND'
            self.hit=pre
        return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--boundary-result',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    bd=json.loads(Path(a.boundary_result).read_text(encoding='utf-8'))
    specs=[]
    with tempfile.TemporaryDirectory(prefix='post_lifecycle_scan_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for r in bd['rows']: z.extract(f"tapes/{int(r['marketId'])}.json.xz",root)
        for r in bd['rows']:
            mid=int(r['marketId']); e=r['branches']['NATIVE']['episodeEnd']; tape=root/'tapes'/f'{mid}.json.xz'
            sim=Scanner(tape,mid,int(e['t']),str(e['reason']))
            try: sim.run_qty('__UNSCORED__')
            finally: sim.close()
            specs.append({'marketId':mid,'boundaryT':int(e['t']),'boundaryReason':str(e['reason']),'state':sim.hit})
            print(json.dumps({'marketId':mid,'boundaryT':int(e['t']),'found':sim.hit is not None,'postT':None if sim.hit is None else sim.hit['t'],'lagMs':None if sim.hit is None else sim.hit['decisionLagAfterBoundaryMsDiagnostic'],'nativeClass':None if sim.hit is None else sim.hit['nativeClass']},ensure_ascii=False),flush=True)
    states=[x['state'] for x in specs if x['state'] is not None]
    out={'version':'MANAGEMENT_MAINLINE_POST_LIFECYCLE_ROLE_SWITCH_SEAMS_V1_20260907','researchOnly':True,'runtimeAuthority':False,'sourceBoundaryResult':a.boundary_result,'marketCount':len(specs),'foundCount':len(states),'rows':specs,'states':states,'boundary':['current V3B native replay','first legal role-switch decision at/after previously observed native structural lifecycle boundary','selection uses no winner/future fill/PnL','decision lag is diagnostic only','no fixed timer/no NEW24-B/no 8781']}
    p=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'marketCount':len(specs),'foundCount':len(states)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
