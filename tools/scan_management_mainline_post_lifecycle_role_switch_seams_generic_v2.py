from __future__ import annotations
import argparse,json,tempfile,zipfile,os,importlib.util
from pathlib import Path
try:
    from tools import run_management_mainline_v3b_closed_loop_role_manager_v1 as cl
except ImportError:
    _p=Path(__file__).with_name('run_management_mainline_v3b_closed_loop_role_manager_v1.py')
    _sp=importlib.util.spec_from_file_location('run_management_mainline_v3b_closed_loop_role_manager_v1',_p)
    cl=importlib.util.module_from_spec(_sp); _sp.loader.exec_module(cl)

REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}
EXPAND_ROLES={'PROBE_CORE','SATELLITE_EXPAND'}

class Scanner(cl.ClosedLoopManager):
    def __init__(self,tape,mid,boundary_t,boundary_reason):
        super().__init__(tape,'NATIVE')
        self.mid=int(mid); self.boundary_t=int(boundary_t); self.boundary_reason=str(boundary_reason); self.hit=None
    def _capture_pre(self,t,e,qv):
        pu=float(self.inv['UP'])-float(self.cost); pd=float(self.inv['DOWN'])-float(self.cost)
        return {
            'marketId':self.mid,'t':int(t),'weakSide':str(e['weakSide']),'expandSide':str(e['expandSide']),
            'repairCandidate':e['repairCandidate'],'expandCandidate':e['expandCandidate'],'freeSlots':int(e['freeSlots']),
            'repairProgressFrac':float(e['repairProgressFrac']),'remainingDebtQty':float(e['remainingDebtQty']),
            'inventory':{'UP':float(self.inv['UP']),'DOWN':float(self.inv['DOWN'])},'cost':float(self.cost),
            'floor':float(min(pu,pd)),'best':float(max(pu,pd)),'liveSlots':len(self.slot_key),
            'liveRepairSlots':sum(1 for _,_,_,r in self._live_role_rows() if r in REPAIR_ROLES),
            'liveExpandSlots':sum(1 for _,_,_,r in self._live_role_rows() if r in EXPAND_ROLES),
            'qLadderLive':self.q_ladder is not None,'qPendingActive':self.q_pending_active is not None,
            'book':{'imbalance':float(qv.get('imb') or 0.0),'spread':float(qv.get('spread') or 0.0),
                    'weakBid':float(qv[str(e['weakSide'])]['bid']),'weakAsk':float(qv[str(e['weakSide'])]['ask']),
                    'expandBid':float(qv[str(e['expandSide'])]['bid']),'expandAsk':float(qv[str(e['expandSide'])]['ask'])},
            'sourceBoundaryT':self.boundary_t,'sourceBoundaryReason':self.boundary_reason,
            'decisionLagAfterBoundaryMsDiagnostic':int(t)-self.boundary_t,
        }
    def _open_one_option(self,t,qv,end):
        native=cl.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option
        if self.hit is not None or int(t)<self.boundary_t:
            return native(self,t,qv,end)
        e=self._eligible(t,qv,end)
        if e is None:
            return native(self,t,qv,end)
        pre=self._capture_pre(t,e,qv); before_n=int(self.n); before_sub=int(self.submits)
        out=native(self,t,qv,end)
        new=[]
        for n in range(before_n,int(self.n)):
            for side in ('UP','DOWN'):
                k=f'{side}_{n}'
                if k in self.orders:
                    role=str(self.key_role.get(k,'UNASSIGNED'))
                    if k in getattr(self,'activeKeys',set()): continue
                    if role in REPAIR_ROLES|EXPAND_ROLES: new.append((k,side,role))
        if int(self.submits)>before_sub and len(new)==1:
            k,side,role=new[0]
            pre['nativeKey']=k; pre['nativeSide']=side; pre['nativeRole']=role
            pre['nativeClass']='REPAIR' if role in REPAIR_ROLES else 'EXPAND'
            self.hit=pre
        return out

def boundary_of(row):
    br=(row.get('branches') or {}).get('NATIVE')
    if isinstance(br,dict) and isinstance(br.get('episodeEnd'),dict):
        e=br['episodeEnd']; return int(e['t']),str(e.get('reason') or 'EPISODE_END')
    br=(row.get('branchResolution') or {}).get('NATIVE')
    if isinstance(br,dict):
        return int(br['t']),str(br.get('kind') or 'BRANCH_RESOLUTION')
    raise KeyError(f'no supported native boundary for market {row.get("marketId")}')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--boundary-result',required=True); ap.add_argument('--output',required=True); ap.add_argument('--max-markets',type=int,default=0); a=ap.parse_args()
    bd=json.loads(Path(a.boundary_result).read_text(encoding='utf-8')); src=list(bd['rows']);
    if int(a.max_markets)>0: src=src[:int(a.max_markets)]
    specs=[]
    with tempfile.TemporaryDirectory(prefix='post_lifecycle_scan_v2_') as td:
        root=Path(td)
        mids=sorted({int(r['marketId']) for r in src})
        with zipfile.ZipFile(a.bundle) as z:
            for mid in mids: z.extract(f'tapes/{mid}.json.xz',root)
        for i,r in enumerate(src,1):
            mid=int(r['marketId']); bt,reason=boundary_of(r); tape=root/'tapes'/f'{mid}.json.xz'
            sim=Scanner(tape,mid,bt,reason)
            try: sim.run_qty('__UNSCORED__')
            finally: sim.close()
            specs.append({'marketId':mid,'boundaryT':bt,'boundaryReason':reason,'state':sim.hit})
            print(json.dumps({'progress':i,'of':len(src),'marketId':mid,'boundaryT':bt,'found':sim.hit is not None,'postT':None if sim.hit is None else sim.hit['t'],'lagMs':None if sim.hit is None else sim.hit['decisionLagAfterBoundaryMsDiagnostic'],'nativeClass':None if sim.hit is None else sim.hit['nativeClass']},ensure_ascii=False),flush=True)
    states=[x['state'] for x in specs if x['state'] is not None]
    out={'version':'MANAGEMENT_MAINLINE_POST_LIFECYCLE_ROLE_SWITCH_SEAMS_GENERIC_V2_20260907','researchOnly':True,'runtimeAuthority':False,'sourceBoundaryResult':a.boundary_result,'marketCount':len(specs),'foundCount':len(states),'rows':specs,'states':states,'boundary':['current V3B native replay','first legal role-switch decision at/after prior native structural lifecycle boundary','supports episodeEnd or branchResolution native boundary','selection uses no winner/future fill/PnL','decision lag diagnostic only','no fixed timer/no NEW24-B/no 8781']}
    p=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'marketCount':len(specs),'foundCount':len(states)},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
