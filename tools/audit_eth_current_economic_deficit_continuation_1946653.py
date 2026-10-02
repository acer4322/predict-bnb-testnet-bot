from __future__ import annotations
import argparse,json,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib,numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_active_expand_generation_debt_attach_1946653 as impl
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_repair_v80_modular_management_kernel as v80
import tools.run_eth_dagger60_smoke_v1 as v1
MID=1946653; pe=base.pe

class CurrentEconomicDeficitContinuationShadow(impl.ActiveExpandGenerationDebtAttachHFT):
 def __init__(self,*a,**kw):
  self.currentDeficitContinuation=[];super().__init__(*a,**kw)
 def _complete_parent_if_structural(self,t,ai):
  before=int(getattr(self,'v80ShareRepairSettlements',0));pid=int(self.repairParent.get('id')) if isinstance(getattr(self,'repairParent',None),dict) and self.repairParent.get('id') is not None else None
  out=super()._complete_parent_if_structural(t,ai)
  after=int(getattr(self,'v80ShareRepairSettlements',0))
  if after>before and getattr(self,'v80EconomicDeficit',None) is not None and self.repairParent is None:
   row={'t':int(t),'event':'CURRENT_ECONOMIC_DEFICIT_CONTINUATION_SHADOW','settledParentId':pid,'floor':float(self._raw_floor()[0]),'deficitAmount':float(self.v80EconomicDeficit.get('amount') or 0.0),'hasThesis':bool(getattr(self,'thesis',None)),'thesisSide':(getattr(self,'thesis',{}) or {}).get('side'),'coordDebt':float(getattr(self,'_coordDebt',0.0) or 0.0),'generationAuthorized':bool(getattr(self,'v70gGenerationAuthorized',False)),'generationId':int(getattr(self,'v70gGenerationId',1)),'lastPayoffRecoveryAt':getattr(self,'lastPayoffRecoveryAt',None)}
   qv=v1.quotes(self.book)
   if qv and getattr(self,'teacher',None) is not None:
    try:
     f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1]);side=self._signal_side(qv);rec=self._v75_recoverability(t,side,qv);ctx=v80.OwnershipContext(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,has_thesis=bool(getattr(self,'thesis',None)),p_expand=pE,signal_side=side,recoverable=bool(rec.get('recoverable')));dec=self.policyProfile.ownership.evaluate(ctx)
     row.update({'secondsLeft':(int(self.capEnd)-int(t))/1000.0,'pExpand':pE,'signalSide':side,'recoverable':bool(rec.get('recoverable')),'recoverabilityReason':rec.get('reason'),'ownershipDecision':dec.reason,'wouldCreateThesis':bool(dec.create_thesis),'ownershipSide':dec.side})
    except Exception as ex:row['evalError']=str(ex)
   # Shadow the existing V30 re-expand gate without mutating state.
   row['existingReexpandGateWouldPass']=bool(getattr(self,'lastPayoffRecoveryAt',None) is not None and int(t)>int(getattr(self,'lastPayoffRecoveryAt')) and int(self.capEnd)-int(t)>180000 and self.reserveBuilder is None and self.repairParent is None and self.outstanding_total()<=1e-9 and float(self._raw_floor()[0])>=-1e-9)
   self.currentDeficitContinuation.append(row)
  return out
 def run_shadow(self,models,winner):
  r=self.run_candidate(models,winner);r['currentEconomicDeficitContinuation']=self.currentDeficitContinuation;return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='current_deficit_cont_1946653_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'CURRENT_ECON_DEFICIT_CONT','elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'CURRENT_ECON_DEFICIT_CONT_START','marketId':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(CurrentEconomicDeficitContinuationShadow,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:r=s.run_shadow(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);pay=s._current_payoffs()
  finally:s.close()
  rows=r.get('currentEconomicDeficitContinuation') or []
  out={'version':'ETH_CURRENT_ECONOMIC_DEFICIT_CONTINUATION_1946653_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'rows':rows,'candidate':base.slim(r),'terminalPayoffs':pay,'allocationParents':parents,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'summary':{'checks':len(rows),'wouldCreateThesis':sum(bool(x.get('wouldCreateThesis')) for x in rows),'recoverable':sum(bool(x.get('recoverable')) for x in rows),'existingReexpandGateWouldPass':sum(bool(x.get('existingReexpandGateWouldPass')) for x in rows)},'boundary':['behavior-inert shadow only','latest EconomicHandoffLeaseLock + confirmed Active Expand generation debt attach','no submit/action mutation','strict-past state only','no Target/winner runtime input','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'rows':rows},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
