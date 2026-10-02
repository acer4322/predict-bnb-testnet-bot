from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path
from collections import Counter

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=1917324;MAX_BLOCKS=5

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

th=sibling('threshold045_earlystop_base',Path(__file__).resolve().with_name('run_eth_continuous_guarded_scheduler_threshold045_smoke1_1917324.py'))
batch=th.batch;v38=th.v38
try:
    from tools.eth_repair_modular import economic_continuous_guarded_scheduler_candidate_v1_profile
    from tools.eth_repair_modular.contracts import GenerationContext
    from tools.eth_repair_modular.generation import PersistentDebtChildEpochGenerationPolicy
except ImportError:
    from eth_repair_modular import economic_continuous_guarded_scheduler_candidate_v1_profile
    from eth_repair_modular.contracts import GenerationContext
    from eth_repair_modular.generation import PersistentDebtChildEpochGenerationPolicy

class EarlyStopProbe(Exception):pass

class GenerationOccupancyProbe(th.Threshold045Candidate):
    def __init__(self,*a,**kw):
        self.generationOccupancyProbeRows=[];self.shadowGenerationPolicy=PersistentDebtChildEpochGenerationPolicy()
        super().__init__(*a,**kw)
    def _probe_generation_block(self,t,admission):
        gid=int(getattr(self,'v70gGenerationId',1));auth=bool(getattr(self,'v70gGenerationAuthorized',False))
        debt=max(0.0,float(getattr(self,'v70dGenerationDebt',0.0) or 0.0)-float(getattr(self,'v70gGenerationDebtBaseline',0.0) or 0.0))
        paid=max(0.0,float(getattr(self,'v70dGenerationPaid',0.0) or 0.0)-float(getattr(self,'v70gGenerationPaidBaseline',0.0) or 0.0))
        remaining=max(0.0,debt-paid);cnt=int(getattr(self,'v70gGenerationResponsibilityCount',{}).get(gid,0))
        carrier_key=getattr(self,'v70gGenerationCarrier',None);entry=getattr(self,'carrierLedger',{}).get(carrier_key,{}) if carrier_key else {}
        submitted=float(entry.get('submittedQty') or 0.0);filled=float(entry.get('actualFilled') or 0.0);carrier_rem=max(0.0,submitted-filled)
        terminal=bool(entry.get('terminalConfirmed'));physical_live=bool(carrier_key and carrier_rem>EPS and not terminal)
        try:equiv=bool(self._expand_occupied())
        except Exception:equiv=False
        ctx=GenerationContext(auth,debt,paid,cnt,physical_expand_live=physical_live,payment_progress_observed=paid>EPS,equivalent_expand_owned=equiv)
        frozen=self.policyProfile.generation.evaluate(ctx);shadow=self.shadowGenerationPolicy.evaluate(ctx)
        rp=getattr(self,'repairParent',None);ths=getattr(self,'thesis',None)
        row={'t':int(t),'secondsLeft':(int(self.capEnd)-int(t))/1000.0,'pExpand':admission.get('pExpand'),'generationId':gid,'generationAuthorized':auth,'responsibilityCount':cnt,'debtIncrement':debt,'paidIncrement':paid,'remainingDebt':remaining,'generationCarrier':carrier_key,'carrierSubmitted':submitted,'carrierFilled':filled,'carrierRemaining':carrier_rem,'carrierTerminalConfirmed':terminal,'physicalExpandLive':physical_live,'equivalentExpandOccupied':equiv,'repairParentId':rp.get('id') if isinstance(rp,dict) else None,'repairParentSide':rp.get('side') if isinstance(rp,dict) else None,'coordDebt':float(getattr(self,'_coordDebt',0.0) or 0.0),'thesisSide':ths.get('side') if isinstance(ths,dict) else None,'frozenGenerationDecision':{'unlocked':bool(frozen.unlocked),'allowNewResponsibility':bool(frozen.allow_new_responsibility),'reason':frozen.reason},'shadowChildEpochDecision':{'unlocked':bool(shadow.unlocked),'allowNewResponsibility':bool(shadow.allow_new_responsibility),'reason':shadow.reason}}
        row['partialDebtSerializationCandidate']=bool((not physical_live) and (not equiv) and paid>EPS and remaining>EPS and (not frozen.unlocked) and shadow.unlocked)
        self.generationOccupancyProbeRows.append(row)
        if len(self.generationOccupancyProbeRows)>=MAX_BLOCKS:raise EarlyStopProbe()
    def _score_state(self,t,after_kind):
        n0=len(getattr(self,'v83Admissions',[]))
        out=super()._score_state(t,after_kind)
        new=list(getattr(self,'v83Admissions',[]))[n0:]
        for row in new:
            if str(row.get('reason'))=='GENERATION_ALREADY_OWNS_EXPAND':self._probe_generation_block(t,row)
        return out

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='generation_occ_earlystop_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'GEN_OCC_EARLYSTOP','ts':time.time(),'market':FIXED}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'GEN_OCC_EARLYSTOP_START','market':FIXED,'maxBlocks':MAX_BLOCKS}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        c=batch.mk(GenerationOccupancyProbe,tape,models,life,cap,tim,econ,price,sur,t44,t47,economic_continuous_guarded_scheduler_candidate_v1_profile())
        stopped=False;run_error=None
        try:
            try:c.run_candidate_guard(models,cr['winner'])
            except EarlyStopProbe:stopped=True
            except Exception as ex:run_error=repr(ex)
            rows=list(c.generationOccupancyProbeRows)
        finally:c.close()
        support=[r for r in rows if r['partialDebtSerializationCandidate']]
        frozen=Counter(r['frozenGenerationDecision']['reason'] for r in rows);shadow=Counter(r['shadowChildEpochDecision']['reason'] for r in rows)
        out={'version':'ETH_THRESHOLD045_GENERATION_OCCUPANCY_EARLYSTOP_SHADOW_1917324','date':'2026-09-04','researchOnly':True,'behaviorAuthority':False,'marketId':FIXED,'thresholdReplay':0.45,'earlyStopped':stopped,'maxBlocks':MAX_BLOCKS,'blocksObserved':len(rows),'runError':run_error,'aggregate':{'partialDebtSerializationCandidates':len(support),'partialDebtSerializationShare':len(support)/len(rows) if rows else 0.0,'physicalExpandLiveBlocks':sum(r['physicalExpandLive'] for r in rows),'equivalentExpandOccupiedBlocks':sum(r['equivalentExpandOccupied'] for r in rows),'confirmedPaymentProgressBlocks':sum(r['paidIncrement']>EPS for r in rows),'remainingDebtBlocks':sum(r['remainingDebt']>EPS for r in rows),'frozenDecisionCounts':dict(frozen),'shadowDecisionCounts':dict(shadow)},'decision':('DIAGNOSTIC_INVALID' if run_error is not None else ('SUPPORT_PARTIAL_DEBT_GENERATION_SERIALIZATION_SEAM' if support else 'NO_SUPPORT_FOR_PARTIAL_DEBT_UNLOCK_FROM_EARLYSTOP_SAMPLE')),'supportRows':support[:30],'rows':rows,'boundary':['early-stop diagnostic prefix only','rejected 0.45 behavior path reproduced until enough generation blocks','PersistentDebtChildEpochGenerationPolicy is shadow only','no shadow decision changes any action','no 8781','no Target future runtime']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':run_error is None,'decision':out['decision'],'earlyStopped':stopped,'aggregate':out['aggregate'],'firstRows':rows[:5]},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
