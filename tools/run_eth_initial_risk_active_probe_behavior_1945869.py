from __future__ import annotations
import argparse,json,os,shutil,tempfile,threading,time,zipfile
from pathlib import Path
import sys,importlib,importlib.util
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
print('IMPORT_STAGE joblib START',flush=True)
import joblib
print('IMPORT_STAGE joblib PASS',flush=True)

def load(fullname,filename):
    print(f'IMPORT_STAGE {fullname} START',flush=True)
    try:
        m=importlib.import_module(fullname);print(f'IMPORT_STAGE {fullname} PASS project',flush=True);return m
    except ImportError:
        p=Path(__file__).with_name(filename);spec=importlib.util.spec_from_file_location(fullname,p)
        if spec is None or spec.loader is None:raise ImportError(p)
        m=importlib.util.module_from_spec(spec);sys.modules[fullname]=m;spec.loader.exec_module(m);print(f'IMPORT_STAGE {fullname} PASS staged',flush=True);return m
load('tools.eth_repair_modular.responsibility_transition','responsibility_transition.py')
load('tools.eth_repair_modular.responsibility_frontier','responsibility_frontier.py')
load('tools.eth_repair_modular.ownership_transition_guard','ownership_transition_guard.py')
load('tools.run_eth_parent_occupancy_anchorless_parallel_ab','run_eth_parent_occupancy_anchorless_parallel_ab.py')
load('tools.run_eth_parent_occupancy_passive_evidence_ab','run_eth_parent_occupancy_passive_evidence_ab.py')
load('tools.run_eth_parent_occupancy_transition_frontier_ab','run_eth_parent_occupancy_transition_frontier_ab.py')
pg=load('tools.run_eth_parent_occupancy_prospective_guard_ab','run_eth_parent_occupancy_prospective_guard_ab.py');pe=pg.pe
EPS=1e-9

class InitialRiskActiveProbeBehavior(pg.ProspectiveGuardParentOccupancyHFT):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.initialActiveProbeEvents=[];self.initialActiveProbeKeys=set();self._usedInitialActiveProbe=False
    def _start_reserve_builder(self,t,qv):
        floor,u,d,cost=self._raw_floor();s=float(qv['UP']['bid'])+float(qv['DOWN']['bid'])
        initial=self.thesis is None
        # Freeze all inherited initial admission requirements; only swap physical route after they pass.
        if not initial:
            return super()._start_reserve_builder(t,qv)
        if self.reserveBuilder is not None or self.repairParent is not None or self.outstanding_total()>EPS:return False
        g=u+d;absr=abs(u-d)/g if g>EPS else 0.0
        if not (0<=floor<1.0 and absr<=.02 and s<1.-EPS):return False
        side=self._signal_side(qv)
        ask=float(qv[side].get('ask') or 0.0)
        if ask<=EPS or ask>=1.0-EPS:return False
        q=1.0/ask
        if q<=EPS or q>12.+EPS:return False
        if int(self.capEnd)-int(t)<=180000:return False
        oid=self._new_objective('EXPAND',side)['id'];n0=self.n
        self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='DIRECTION_OPEN_FIRST'
        ok=self.submit(t,side,ask,q)
        if not ok:return False
        key=f'{side}_{n0}';self.initialActiveProbeKeys.add(key);self._usedInitialActiveProbe=True
        self.thesis={'id':self.nextThesisId,'side':side,'bornAt':int(t),'materialized':False,'recoveries':0,'opens':1};self.nextThesisId+=1
        self.thesisOpenSubmits+=1;self.reserveFirstLegSubmit+=1;self.reserveFirstKeys.append(key)
        self.reserveBuilder={'id':self.nextReserveBuilderId,'firstKey':key,'firstSide':side,'firstPrice':ask,'firstQty':q,'submittedAt':int(t),'firstFillAt':None,'floorBefore':floor,'pairDeadline':None,'completed':False,'thesisId':self.thesis['id'],'isReexpand':False};self.nextReserveBuilderId+=1
        start=int(self.capEnd)-300000
        ev={'t':int(t),'event':'INITIAL_RISK_ACTIVE_PROBE_SUBMIT','key':key,'side':side,'ask':ask,'qty':q,'grossCost':ask*q,'normalizedPhase':(int(t)-start)/300000.0,'floorBefore':float(floor)}
        self.initialActiveProbeEvents.append(ev);self.thesisEvents.append({'t':int(t),'event':'OPEN_DIRECTION_SUBMIT','side':side,'price':ask,'qty':q,'floor':floor,'physicalRoute':'ACTIVE_PROBE'})
        return True
    def process(self,t):
        before=len(getattr(self,'v53Fills',[]) or [])
        super().process(t)
        fills=(getattr(self,'v53Fills',[]) or [])[before:]
        for e in fills:
            if e.get('key') in self.initialActiveProbeKeys:
                self.initialActiveProbeEvents.append({'t':int(e.get('t') or t),'event':'INITIAL_RISK_ACTIVE_PROBE_FILL','key':e.get('key'),'side':e.get('side'),'price':float(e.get('price') or 0),'qty':float(e.get('qty') or 0),'parentId':e.get('parentId')})
    def run_behavior(self,models,winner):
        r=self.run_guard(models,winner);r['initialActiveProbeEvents']=self.initialActiveProbeEvents;r['initialActiveProbeKeys']=sorted(self.initialActiveProbeKeys);return r

def slim(r):
    return {'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0),'floor':float(r.get('floor') or 0),'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'parallelRepairSubmits':int(r.get('parallelRepairSubmits') or 0),'parallelRepairActiveFillQty':float(r.get('parallelRepairActiveFillQty') or 0),'overflowAllocatedQty':float(r.get('v84OverflowAllocatedQty') or 0),'overflowPaidQty':float(r.get('v84OverflowPaidQty') or 0)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=1945869);ap.add_argument('--output',required=True);a=ap.parse_args();mid=a.market_id
    outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);tmp=Path(tempfile.mkdtemp(prefix='initial_active_behavior_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'INITIAL_RISK_ACTIVE_BEHAVIOR','market':mid,'ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'INITIAL_RISK_ACTIVE_BEHAVIOR_START','market':mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz'
        control=pe.make(pg.ProspectiveGuardParentOccupancyHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:br=control.run_guard(models,cr['winner'])
        finally:control.close()
        cand=pe.make(InitialRiskActiveProbeBehavior,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:
            rr=cand.run_behavior(models,cr['winner']);cons,bound,parents=pe.alloc(cand,rr)
        finally:cand.close()
        ss=pe.safety(rr);safe=all(float(v or 0)<=EPS for v in ss.values())
        probe_fills=[e for e in rr.get('initialActiveProbeEvents',[]) if e.get('event')=='INITIAL_RISK_ACTIVE_PROBE_FILL'];probe_submits=[e for e in rr.get('initialActiveProbeEvents',[]) if e.get('event')=='INITIAL_RISK_ACTIVE_PROBE_SUBMIT']
        repair_submits=[e for e in (rr.get('submitTrace') or []) if e.get('pendingRole')=='REPAIR']
        repair_fills=[e for e in (rr.get('v53FillEvents') or rr.get('v53Fills') or []) if e.get('objectiveRole')=='REPAIR' or e.get('executionRole')=='REPAIR']
        physical=sum(float(e.get('qty') or 0) for e in probe_fills)
        gross=max([float(e.get('grossCost') or 0) for e in probe_submits] or [0.0])
        gates={'initialActivePhysicalFill':physical>EPS,'initialGrossCostLe1':gross<=1.000001,'repairParentBirth':int(rr.get('repairParentBirths') or 0)>0,'repairSubmitAfterInitialFill':len(repair_submits)>0,'repairActualFillAfterInitialFill':len(repair_fills)>0,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound)}
        if not safe or not cons or not bound or gross>1.000001:decision='SAFETY_OR_ACCOUNTING_REJECT'
        elif physical<=EPS:decision='NO_INITIAL_ACTIVE_FILL'
        elif not gates['repairSubmitAfterInitialFill']:decision='INITIAL_ACTIVE_FILL_BUT_NO_REPAIR_PATH'
        elif not gates['repairActualFillAfterInitialFill']:decision='INITIAL_ACTIVE_FILL_REPAIR_SUBMIT_NO_FILL'
        else:decision='FULL_INITIAL_RISK_TO_REPAIR_FUNCTIONAL_PASS'
        out={'version':'INITIAL_RISK_ACTIVE_PROBE_BEHAVIOR_1945869_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'control':slim(br),'candidate':slim(rr),'delta':{'pnl':float(rr.get('pnlDiagnosticOnly') or 0)-float(br.get('pnlDiagnosticOnly') or 0),'floor':float(rr.get('floor') or 0)-float(br.get('floor') or 0),'fills':int(rr.get('actualFillEvents') or 0)-int(br.get('actualFillEvents') or 0),'rounds':int(rr.get('v70dSemanticRounds') or rr.get('rounds') or 0)-int(br.get('v70dSemanticRounds') or br.get('rounds') or 0)},'initialActiveProbeEvents':rr.get('initialActiveProbeEvents',[]),'repairSubmits':repair_submits[:30],'repairFills':repair_fills[:30],'safety':ss,'allocationParents':parents,'boundary':['same OUR direction as control','only initial physical route changes passive bid -> same-clock minimum-legal ask','~1 USDT gross initial risk','no fixed wait seconds','all downstream management frozen','Target side not used at runtime','no dream fill','no 8781']}
        outp.parent.mkdir(parents=True,exist_ok=True);outp.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'control':out['control'],'candidate':out['candidate'],'delta':out['delta']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
