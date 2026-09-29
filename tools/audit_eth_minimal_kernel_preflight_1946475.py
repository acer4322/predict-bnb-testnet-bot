from __future__ import annotations
import argparse,json,math,shutil,sys,tempfile,threading,time,zipfile
from pathlib import Path
import joblib,numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_minimal_kernel_economic_deficit_priority_ab as mk
MID=1946475;EPS=1e-9

class Trace(mk.MinimalKernelEconomicDeficitPriorityHFT):
    def __init__(self,*a,**kw):
        self.preflightTrace=[];super().__init__(*a,**kw)
    def _score_state(self,t,after_kind):
        row={'t':int(t),'afterKind':after_kind,'coordDebt':float(getattr(self,'_coordDebt',0.0) or 0.0),'hasRepairParent':isinstance(getattr(self,'repairParent',None),dict),'genAuthorizedBefore':bool(getattr(self,'v70gGenerationAuthorized',False)),'thesisSide':(getattr(self,'thesis',{}) or {}).get('side'),'economicDeficit':dict(self.v80EconomicDeficit) if isinstance(getattr(self,'v80EconomicDeficit',None),dict) else None}
        try:
            qv=mk.qfn(self.book);row['hasQuotes']=bool(qv)
            if qv and getattr(self,'teacher',None) is not None:
                f=self._coord_feature(int(t));x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);row['pExpand']=float(self.teacher['model'].predict_proba(x)[0,1]);side=row['thesisSide']
                if side in ('UP','DOWN'):
                    row['candidateBid']=qv[side].get('bid');row['candidateAsk']=qv[side].get('ask')
                    try:row['recoverability']=self._v75_recoverability(int(t),side,qv)
                    except Exception as ex:row['recoverabilityError']=repr(ex)
            try:row['expandOccupiedBefore']=bool(self._expand_occupied())
            except Exception as ex:row['expandOccupiedError']=repr(ex)
        except Exception as ex:row['snapshotError']=repr(ex)
        b=len(getattr(self,'v83Admissions',[]));pc0=int(getattr(self,'portfolioPriorityChecks',0));pb0=int(getattr(self,'portfolioPriorityBlocks',0))
        out=super()._score_state(t,after_kind)
        new=(getattr(self,'v83Admissions',[]) or [])[b:]
        row.update({'genAuthorizedAfter':bool(getattr(self,'v70gGenerationAuthorized',False)),'portfolioChecksDelta':int(getattr(self,'portfolioPriorityChecks',0))-pc0,'portfolioBlocksDelta':int(getattr(self,'portfolioPriorityBlocks',0))-pb0,'newV83Admissions':new,'returnedAction':bool(out)})
        if len(self.preflightTrace)<1200:self.preflightTrace.append(row)
        return out
    def run_trace(self,models,winner):
        r=self.run_kernel(models,winner);r['preflightTrace']=self.preflightTrace;return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='mk_preflight_1946475_'));stop=threading.Event();started=time.time()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'MK_PREFLIGHT_1946475','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MK_PREFLIGHT_1946475_START'}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=mk.pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        s=mk.pe.make(Trace,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_trace(models,cr['winner']);m=mk.sm(s,r)
        finally:s.close()
        submits=[x for x in r.get('preflightTrace',[]) if any(bool(z.get('submit')) for z in (x.get('newV83Admissions') or []))]
        around=[x for x in r.get('preflightTrace',[]) if 1788535285000<=int(x.get('t') or 0)<=1788535288000]
        out={'version':'MINIMAL_KERNEL_PREFLIGHT_TRACE_1946475_V1','researchOnly':True,'behaviorChange':False,'marketId':MID,'metrics':mk.compact(m),'priorityEvents':r.get('portfolioPriorityEvents',[]),'v83SubmitTraceRows':submits,'aroundHarmfulClock':around,'traceCount':len(r.get('preflightTrace',[]))}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'metrics':out['metrics'],'submitRows':submits,'around':around},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
