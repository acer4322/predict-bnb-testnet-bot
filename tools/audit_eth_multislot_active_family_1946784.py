from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys,time,threading
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
MID=1946784; pe=base.pe

def slim_carriers(s,pid):
    out=[]
    for k,e in getattr(s,'carrierLedger',{}).items():
        try:
            if int(e.get('parentId'))!=int(pid): continue
        except Exception: continue
        if str(e.get('objectiveRole') or '').upper()!='REPAIR': continue
        o=getattr(s,'orders',{}).get(k,{})
        out.append({'key':str(k),'lane':str(e.get('lane') or ''),'side':e.get('side'),'objectiveId':e.get('objectiveId'),'submittedQty':float(e.get('submittedQty') or 0.0),'actualFilled':float(e.get('actualFilled') or 0.0),'terminalConfirmed':bool(e.get('terminalConfirmed')),'cancelRequested':bool(e.get('cancelRequested')),'price':float(o.get('price') or e.get('price') or 0.0),'placed':o.get('placed')})
    return out

def epoch_state(s,pid):
    st=getattr(s,'generationEpochByParent',{}).get(pid)
    if st is None:return None
    return {k:getattr(st,k,None) for k in ['parent_id','epoch','armed','active_owned','parent_fill_base','parent_churn_base','parent_paid_base']}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='audit_multislot_family_'));stop=threading.Event();started=time.time()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'AUDIT_MULTISLOT_ACTIVE_FAMILY_1946784','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'AUDIT_MULTISLOT_ACTIVE_FAMILY_START','marketId':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};cr=co[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        s=pe.make(mod.EconomicHandoffLeaseLockHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:
            r=s.run_locked(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);pid=int(getattr(s,'repairParent',{}).get('id') or 1)
            state={'activeByParent':{str(k):dict(v) for k,v in getattr(s,'activeByParent',{}).items()},'familyCarriers':slim_carriers(s,pid),'epoch':epoch_state(s,pid),'hardConfirmed':sorted(int(x) for x in getattr(s,'hardConfirmed',set())),'armedParents':sorted(int(x) for x in getattr(s,'_armedParents',set()))}
        finally:s.close()
        out={'version':'AUDIT_ETH_MULTISLOT_ACTIVE_FAMILY_1946784_V1','date':'2026-09-05','researchOnly':True,'behaviorInert':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'summary':base.slim(r),'safety':pe.safety(r),'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'allocationParents':parents,'state':state,'activeEvents':(r.get('v36ActiveEvents') or [])[:300],'v51Events':(r.get('v51Events') or [])[:300],'v52Events':(r.get('v52Events') or [])[:300],'generationStarts':(r.get('v47GenerationStarts') or [])[:100],'economicEvents':(r.get('economicHandoffLeaseEvents') or [])[:200],'rollingEvents':(r.get('repeatedRollingEvents') or [])[:300],'partitionFills':r.get('partitionFills') or [],'boundary':['behavior-inert trace only','same frozen EconomicHandoffLeaseLockHFT candidate as replication3b','no strategy change','no Target runtime input','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'safety':out['safety'],'activeByParent':state['activeByParent'],'familyCarriers':state['familyCarriers'],'epoch':state['epoch'],'v51Events':out['v51Events'][:20],'activeEvents':out['activeEvents'][:20]},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
