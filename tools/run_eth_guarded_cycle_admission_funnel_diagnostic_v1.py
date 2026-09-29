from __future__ import annotations
import argparse, collections, importlib.util, json, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

batch=sibling('guarded_cycle_batch_for_funnel_v1',Path(__file__).resolve().with_name('run_eth_continuous_guarded_scheduler_modular_batch.py'))
Candidate=batch.Candidate
front=batch.front
v38=batch.v38

class AdmissionFunnelDiagnostic(Candidate):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.funnelReasonCounts=collections.Counter();self.funnelSchedulerReasonCounts=collections.Counter();self.funnelRows=[]
    def _score_state(self,t,after_kind):
        source=getattr(self,'_schedulerClockSource',None) or 'EVENT_CLOCK'
        before=len(getattr(self,'v83Admissions',[]))
        gen_before=bool(getattr(self,'v70gGenerationAuthorized',False))
        try: occupied_before=bool(self._expand_occupied())
        except Exception: occupied_before=False
        thesis=getattr(self,'thesis',None)
        th_side=thesis.get('side') if isinstance(thesis,dict) else None
        rp=getattr(self,'repairParent',None)
        parent_id=rp.get('id') if isinstance(rp,dict) else None
        coord=float(getattr(self,'_coordDebt',0.0) or 0.0)
        out=super()._score_state(t,after_kind)
        new=list(getattr(self,'v83Admissions',[]))[before:]
        for row in new:
            reason=str(row.get('reason') or 'UNKNOWN')
            self.funnelReasonCounts[reason]+=1
            if source!='EVENT_CLOCK': self.funnelSchedulerReasonCounts[reason]+=1
            rec=row.get('recoverability') if isinstance(row.get('recoverability'),dict) else {}
            self.funnelRows.append({
                't':int(t),'clockSource':source,'afterKind':after_kind,'reason':reason,'submit':bool(row.get('submit')),
                'pExpand':row.get('pExpand'),'side':row.get('side'),'parentId':row.get('parentId',parent_id),
                'generationAuthorizedBefore':gen_before,'expandOccupiedBefore':occupied_before,'thesisSideBefore':th_side,
                'coordDebtBefore':coord,'recoverabilityReason':rec.get('reason'),'recoverable':rec.get('recoverable'),
                'recoverability':rec,'key':row.get('key'),'objectiveId':row.get('objectiveId')
            })
        return out
    def run_diag(self,models,winner):
        r=self.run_candidate_guard(models,winner)
        r.update({'funnelReasonCounts':dict(self.funnelReasonCounts),'funnelSchedulerReasonCounts':dict(self.funnelSchedulerReasonCounts),'funnelRows':self.funnelRows[:1800]})
        return r

def pct(xs,q):
    if not xs:return None
    ys=sorted(xs);pos=(len(ys)-1)*q;lo=int(pos);hi=min(lo+1,len(ys)-1);a=pos-lo;return ys[lo]*(1-a)+ys[hi]*a

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='guarded_cycle_funnel_v1_'));stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'GUARDED_CYCLE_ADMISSION_FUNNEL_V1','market':mid,'ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'GUARDED_CYCLE_ADMISSION_FUNNEL_V1_START','market':mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);by={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};cr=by[mid]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        sim=AdmissionFunnelDiagnostic(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=batch.economic_continuous_guarded_scheduler_candidate_v1_profile())
        try:r=sim.run_diag(models,cr['winner']);ss=front.safety(r)
        finally:sim.close()
        rows=list(r.get('funnelRows') or []);sched=[x for x in rows if x['clockSource']!='EVENT_CLOCK'];ps=[float(x['pExpand']) for x in sched if x.get('pExpand') is not None]
        byreason={}
        for reason in sorted(set(x['reason'] for x in sched)):
            z=[x for x in sched if x['reason']==reason];zp=[float(x['pExpand']) for x in z if x.get('pExpand') is not None]
            byreason[reason]={'n':len(z),'pExpandMedian':pct(zp,.5),'pExpandP10':pct(zp,.1),'pExpandP90':pct(zp,.9),'firstT':min((x['t'] for x in z),default=None),'lastT':max((x['t'] for x in z),default=None),'recoverabilityReasons':dict(collections.Counter(str(x.get('recoverabilityReason') or 'NONE') for x in z))}
        out={'version':'ETH_GUARDED_CYCLE_ADMISSION_FUNNEL_DIAGNOSTIC_V1','date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,'marketId':mid,'winnerPostHocOnly':cr['winner'],'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'floor':r.get('floor'),'fills':r.get('actualFillEvents'),'rounds':r.get('v70dSemanticRounds'),'scheduler':r.get('schedulerAdapter'),'reasonCounts':r.get('funnelReasonCounts'),'schedulerReasonCounts':r.get('funnelSchedulerReasonCounts'),'schedulerChecks':len(sched),'schedulerPExpand':{'median':pct(ps,.5),'p10':pct(ps,.1),'p90':pct(ps,.9),'max':max(ps) if ps else None},'schedulerByReason':byreason,'rows':rows,'safety':ss,'boundary':['diagnostic instrumentation only','candidate behavior identical to guarded continuous scheduler','no threshold/qty/price/model changes','winner/PnL post-hoc only','realistic HFT only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'marketId':mid,'reasonCounts':out['reasonCounts'],'schedulerReasonCounts':out['schedulerReasonCounts'],'schedulerPExpand':out['schedulerPExpand'],'schedulerByReason':byreason,'safety':ss},ensure_ascii=False),flush=True)
    finally: stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
