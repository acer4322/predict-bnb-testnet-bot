from __future__ import annotations
import argparse, importlib.util, json, os, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
MID=1917552

def load(name, fn):
    p=Path(__file__).with_name(fn); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m

front=load('first_handoff_for_role_truth_1917552','run_eth_alignment_first_active_handoff_frontier_1917552.py')
exec2x2=front.exec2x2; base=front.base; v38=front.v38

class RoleTruthTrace(front.FirstActiveHandoffFrontier):
    def __init__(self,*a,**kw):
        self.roleTruthTrace=[]
        super().__init__(*a,**kw)
    def submit(self,t,side,p,q):
        before=int(getattr(self,'authorizedSubmitWithTruthRoleMismatch',0) or 0)
        n_before=int(self.n)
        pending_role=getattr(self,'_pendingAuthorizedRole',None)
        pending_oid=getattr(self,'_pendingAuthorizedObjectiveId',None)
        truth_inv=dict(getattr(self,'truthInv',{})); mgr_inv=dict(getattr(self,'inv',{}))
        rp=dict(self.repairParent) if isinstance(getattr(self,'repairParent',None),dict) else None
        ok=super().submit(t,side,p,q)
        if not ok: return ok
        key=f'{side}_{n_before}'
        after=int(getattr(self,'authorizedSubmitWithTruthRoleMismatch',0) or 0)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or getattr(self,'capEnd',0) or 0)
        start=end-300000 if end else 0
        row={
            't':int(t),'phasePct':((int(t)-start)/3000.0 if start else None),'key':key,'side':side,'price':float(p),'qty':float(q),
            'authorizedRole':getattr(self,'submitRoleAuthorized',{}).get(key,pending_role),
            'observedRole':getattr(self,'submitRoleObserved',{}).get(key),
            'truthRole':getattr(self,'submitRoleTruth',{}).get(key),
            'pendingAuthorizedRole':pending_role,'objectiveId':pending_oid,
            'repairParentId':(int(rp.get('id')) if rp and rp.get('id') is not None else None),
            'repairParentSide':(rp.get('side') if rp else None),
            'truthInvBefore':truth_inv,'managerInvBefore':mgr_inv,
            'mismatchCounterBefore':before,'mismatchCounterAfter':after,'mismatchIncrement':max(0,after-before),
        }
        self.roleTruthTrace.append(row)
        return ok
    def run_trace(self,models,winner):
        r=self.run_frontier(models,winner)
        r['roleTruthSubmitTrace']=self.roleTruthTrace
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    if a.market_id!=MID: raise ValueError(a.market_id)
    outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);outp.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='role_truth_1917552_'));stop=threading.Event()
    def hb():
        while not stop.wait(10): print(json.dumps({'heartbeat':'ROLE_TRUTH_1917552','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'ROLE_TRUTH_1917552_START'}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a)
        teacher=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; gen=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        sim=base.make_simulator(RoleTruthTrace,tmp/'tapes'/f'{MID}.json.xz',models,life,cap,tim,econ,price,sur,teacher,gen)
        try:r=sim.run_trace(models,cohort[MID]['winner'])
        finally:sim.close()
        tr=r.get('roleTruthSubmitTrace',[]) or []; mm=[x for x in tr if int(x.get('mismatchIncrement') or 0)>0]
        out={
            'version':'ROLE_TRUTH_MISMATCH_1917552_RESULT_20260904','date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,
            'marketId':MID,'behaviorMutation':False,
            'decision':'MISMATCH_LOCALIZED' if len(mm)==int(r.get('authorizedSubmitWithTruthRoleMismatch') or 0) else 'MISMATCH_TRACE_INCOMPLETE',
            'summary':{'submits':int(r.get('submits') or 0),'fills':int(r.get('actualFillEvents') or 0),'truthMismatch':int(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'tracedSubmits':len(tr),'tracedMismatchIncrements':sum(int(x.get('mismatchIncrement') or 0) for x in tr)},
            'mismatchEvents':mm,'submitTrace':tr,
            'safety':exec2x2.safety_summary(r,True),
            'boundary':['exact baseline behavior; instrumentation only','no Target runtime input','normalized phase descriptive only','no 8781']
        }
        outp.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'summary':out['summary'],'mismatchEvents':mm},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
