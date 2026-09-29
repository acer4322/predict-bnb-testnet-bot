from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p)
 if s is None or s.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v80=sib('eth_v80_for_v82','run_eth_repair_v80_modular_management_kernel.py');v38=v80.v38;v1=v80.v1

class V80NoCoord(v80.V80ModularManagementKernel):
 def _score_state(self,t,after_kind): return None
class V80NoIncrementalNoCoord(V80NoCoord):
 def _repair_payoff_budget(self,p):
  return v38.v36.V36EventConfirmedActive._repair_payoff_budget(self,p)
class V80OwnershipOnly(v80.V80ModularManagementKernel):
 def _score_state(self,t,after_kind):
  if after_kind=='REPAIR' and getattr(self,'thesis',None) is None and self._coordDebt>EPS and self.repairParent is not None and self.teacher is not None:
   f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1]);qv=v1.quotes(self.book)
   if qv:
    side=self._signal_side(qv);rec=self._v75_recoverability(t,side,qv);ctx=v80.OwnershipContext(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,has_thesis=False,p_expand=pE,signal_side=side,recoverable=bool(rec.get('recoverable')));dec=self.policyProfile.ownership.evaluate(ctx)
    row={**rec,'event':'V82_OWNERSHIP_ONLY_CHECK','pExpand':pE,'signalSide':side,'decision':dec.reason,'createThesis':bool(dec.create_thesis)}
    if dec.create_thesis:
     self.thesis={'id':self.nextThesisId,'side':dec.side,'bornAt':int(t),'materialized':False,'recoveries':0,'opens':0,'birthKind':'V82_OWNERSHIP_ONLY'};self.nextThesisId+=1;self.v75Births+=1;self.v75Recoverable+=1;row['thesisId']=self.thesis['id']
    self.v75Checks+=1;self.v75Events.append(dict(row));self.v80OwnershipEvents.append(row)
  return None
class V80V36OnlyActive(v80.V80ModularManagementKernel):
 def _maybe_hard_active(self,t):
  return v38.v36.V36EventConfirmedActive._maybe_hard_active(self,t)
class V80NoActiveExpandFallback(v80.V80ModularManagementKernel):
 def _submit_active_expand(self,t,source_key,e,o,remaining): return False

def safety(r):
 return sum(float(r.get(k) or 0.0) for k in ['authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','v51ResponsibilityOverfill','repairToExpandAtFirstFill','v70dPreBirthPaymentLeak','v70dDuplicateGenerationDebt'])
def instantiate(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47):
 return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
def runone(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47,winner):
 s=instantiate(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47)
 try:return s.run_exam_v80(models,winner)
 finally:s.close()

def main():
 ap=argparse.ArgumentParser();
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--lane',choices=['A','B'],required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 midsA=[1823894,1824747,1827223,1827903];midsB=[1823894,1824747,1823769,1827223,1827903];mids=midsA if a.lane=='A' else midsB
 tmp=Path(tempfile.mkdtemp(prefix=f'eth_v82_{a.lane.lower()}_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':f'V82_LANE_{a.lane}','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':f'V82_LANE_{a.lane}_START','markets':mids}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co}
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
  rows=[]
  if a.lane=='A':
   for mid in mids:
    cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
    inc0=runone(V80NoIncrementalNoCoord,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr['winner']);inc1=runone(V80NoCoord,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr['winner'])
    v440=runone(V80OwnershipOnly,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr['winner']);v441=runone(v80.V80ModularManagementKernel,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr['winner'])
    rows.append({'marketId':mid,'v38Off':inc0,'v38On':inc1,'v44Off':v440,'v44On':v441})
    print(json.dumps({'marketId':mid,'v38':{'caps':inc1.get('v38IncrementalQtyCapEvents'),'floor':[inc0.get('floor'),inc1.get('floor')]},'v44':{'fills':v441.get('v44ActualFillQty'),'repairAfter':v441.get('v44RepairQtyAfterExpand'),'floor':[v440.get('floor'),v441.get('floor')]}},ensure_ascii=False),flush=True)
   inc_ex=sum(int(x['v38On'].get('v38IncrementalQtyCapEvents') or 0) for x in rows);inc0=sum(float(x['v38Off'].get('floor') or 0) for x in rows);inc1=sum(float(x['v38On'].get('floor') or 0) for x in rows)
   inc_nonw=sum(float(x['v38On'].get('floor') or 0)+EPS>=float(x['v38Off'].get('floor') or 0) for x in rows);inc_safe=all(safety(x['v38On'])<=EPS for x in rows)
   v44fill=sum(float(x['v44On'].get('v44ActualFillQty') or 0) for x in rows);v440=sum(float(x['v44Off'].get('floor') or 0) for x in rows);v441=sum(float(x['v44On'].get('floor') or 0) for x in rows);v44_nonw=sum(float(x['v44On'].get('floor') or 0)+EPS>=float(x['v44Off'].get('floor') or 0) for x in rows);v44_safe=all(safety(x['v44On'])<=EPS for x in rows)
   modules={
    'V38_INCREMENTAL_REPAIR':{'exercised':inc_ex>0,'baselineFloorSum':inc0,'candidateFloorSum':inc1,'perMarketNonWorse':inc_nonw,'markets':len(rows),'safety':inc_safe,'screen':'ACTION_SALVAGE_CANDIDATE' if inc_ex>0 and inc_safe and inc_nonw==len(rows) else 'KEEP_AS_SIZING_FEATURE_RETEST' if inc_ex>0 and inc_safe else 'DO_NOT_SALVAGE'},
    'V44_PARALLEL_EXPAND':{'exercised':v44fill>EPS,'fillQty':v44fill,'baselineFloorSum':v440,'candidateFloorSum':v441,'perMarketNonWorse':v44_nonw,'markets':len(rows),'safety':v44_safe,'screen':'ACTION_SALVAGE_CANDIDATE' if v44fill>EPS and v44_safe and v44_nonw==len(rows) else 'KEEP_OPPORTUNITY_SIGNAL_REPLACE_ADMISSION' if v44fill>EPS and v44_safe else 'DO_NOT_SALVAGE'}
   }
  else:
   for mid in mids:
    cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
    ga0=runone(V80V36OnlyActive,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr['winner']);ga1=runone(v80.V80ModularManagementKernel,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr['winner'])
    ax0=runone(V80NoActiveExpandFallback,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr['winner']);ax1=runone(v80.V80ModularManagementKernel,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr['winner'])
    rows.append({'marketId':mid,'genActiveOff':ga0,'genActiveOn':ga1,'activeExpandOff':ax0,'activeExpandOn':ax1})
    print(json.dumps({'marketId':mid,'genActive':{'paid':ga1.get('v49GenerationActivePaidQty'),'blocks':ga1.get('v50StrandingBlocks'),'retained':ga1.get('v51RetainedPassive'),'epochs':ga1.get('v52EpochResets'),'floor':[ga0.get('floor'),ga1.get('floor')]},'activeExpand':{'fill':ax1.get('v64FillQty'),'blocks':ax1.get('v76ActiveBlocks'),'rounds':[ax0.get('v70dSemanticRounds'),ax1.get('v70dSemanticRounds')],'floor':[ax0.get('floor'),ax1.get('floor')]}},ensure_ascii=False),flush=True)
   ga_ex=sum(float(x['genActiveOn'].get('v49GenerationActivePaidQty') or 0) for x in rows);ga_blocks=sum(int(x['genActiveOn'].get('v50StrandingBlocks') or 0) for x in rows);ga_retain=sum(int(x['genActiveOn'].get('v51RetainedPassive') or 0) for x in rows);ga_epoch=sum(int(x['genActiveOn'].get('v52EpochResets') or 0) for x in rows);ga0=sum(float(x['genActiveOff'].get('floor') or 0) for x in rows);ga1=sum(float(x['genActiveOn'].get('floor') or 0) for x in rows);ga_nonw=sum(float(x['genActiveOn'].get('floor') or 0)+EPS>=float(x['genActiveOff'].get('floor') or 0) for x in rows);ga_safe=all(safety(x['genActiveOn'])<=EPS for x in rows)
   axfill=sum(float(x['activeExpandOn'].get('v64FillQty') or 0) for x in rows);axblocks=sum(int(x['activeExpandOn'].get('v76ActiveBlocks') or 0) for x in rows);ax0=sum(float(x['activeExpandOff'].get('floor') or 0) for x in rows);ax1=sum(float(x['activeExpandOn'].get('floor') or 0) for x in rows);ax_nonw=sum(float(x['activeExpandOn'].get('floor') or 0)+EPS>=float(x['activeExpandOff'].get('floor') or 0) for x in rows);ax_safe=all(safety(x['activeExpandOn'])<=EPS for x in rows)
   debt=sum(float(x['activeExpandOn'].get('v48GenerationDebtQty') or 0) for x in rows);paid=sum(float(x['activeExpandOn'].get('v48GenerationPaidQty') or 0) for x in rows);over=sum(float(x['activeExpandOn'].get('v48GenerationOverpay') or 0) for x in rows);pre=sum(float(x['activeExpandOn'].get('v48PreBirthRepairLeak') or 0) for x in rows)
   modules={
    'V49_V52_GENERATION_ACTIVE_REPAIR_STACK':{'exercised':ga_ex>EPS or ga_blocks>0 or ga_retain>0 or ga_epoch>0,'activePaidQty':ga_ex,'strandingBlocks':ga_blocks,'retainedPassive':ga_retain,'epochResets':ga_epoch,'baselineFloorSum':ga0,'candidateFloorSum':ga1,'perMarketNonWorse':ga_nonw,'markets':len(rows),'safety':ga_safe,'screen':'ACTION_SALVAGE_CANDIDATE' if ga_safe and ga_nonw==len(rows) and ga_ex>EPS else 'KEEP_SUBMODULES_RETEST_SEPARATELY' if ga_safe else 'DO_NOT_SALVAGE'},
    'V64_V65_V70G_ACTIVE_EXPAND_OWNERSHIP_STACK':{'exercised':axfill>EPS or axblocks>0,'activeFillQty':axfill,'recoverabilityBlocks':axblocks,'baselineFloorSum':ax0,'candidateFloorSum':ax1,'perMarketNonWorse':ax_nonw,'markets':len(rows),'safety':ax_safe,'screen':'ACTION_SALVAGE_CANDIDATE' if ax_safe and ax_nonw==len(rows) and axfill>EPS else 'KEEP_EXECUTION_OWNERSHIP_WITH_GATE' if ax_safe else 'DO_NOT_SALVAGE'},
    'V48_GENERATION_LEDGER':{'generationDebtQty':debt,'generationPaidQty':paid,'generationOverpay':over,'preBirthRepairLeak':pre,'safety':over<=EPS and pre<=EPS,'screen':'KEEP_ACCOUNTING_SUBSTRATE' if over<=EPS and pre<=EPS else 'FIX_LEDGER'}
   }
  out={'version':f'ETH_REPAIR_V82_QUICK_LEGACY_MODULE_SALVAGE_SCREEN_LANE_{a.lane}','date':'2026-09-03','researchOnly':True,'lane':a.lane,'modules':modules,'rows':rows,'boundary':['quick salvage screen only','same V80 management authority','no tuning','realistic HFT only','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'lane':a.lane,'modules':modules},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
