from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v70g=sib('eth_v70g_for_v73f','run_eth_repair_v70g_generation_scoped_relay_hft_smoke.py');v65=v70g.v65;v38=v70g.v38

class V73FFamilyRelay(v70g.V70GGenerationScopedRelay):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v73fFamilies=[];self.v73fChildKeys=set()
 def _submit_active_expand(self,t,source_key,e,o,remaining):
  before=set(getattr(self,'v64Active',{}).keys());ok=super()._submit_active_expand(t,source_key,e,o,remaining)
  if not ok:return ok
  after=set(getattr(self,'v64Active',{}).keys());new=[k for k in after-before if str(self.v64Active.get(k,{}).get('sourceKey'))==str(source_key)]
  for child in new:
   ce=self.carrierLedger.get(child,{})
   same=bool(self.v70gGenerationAuthorized and str(self.v70gGenerationCarrier)==str(source_key) and str(ce.get('objectiveRole'))=='EXPAND' and str(ce.get('side'))==str(e.get('side')))
   row={'t':int(t),'sourceKey':source_key,'childKey':child,'generation':self.v70gGenerationId,'sameResponsibilityFamily':same,'sourceAuthorizedQty':float(e.get('submittedQty') or o.get('qty') or 0.0),'childSubmittedQty':float(ce.get('submittedQty') or 0.0),'side':ce.get('side')}
   if same:
    self.v70dKeys.add(child);self.v70dFillSeen[child]=float(ce.get('actualFilled') or 0.0);self.v73fChildKeys.add(child);row['registeredForGenerationDebt']=True
   else:row['registeredForGenerationDebt']=False
   self.v73fFamilies.append(row)
  return ok
 def run_exam_v73f(self,models,winner):
  r=super().run_exam_v70g(models,winner);self._refresh_carrier_ledger(int(self.capEnd));r.update({'v73fResponsibilityFamilies':self.v73fFamilies,'v73fChildKeys':sorted(self.v73fChildKeys)});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=1825353:raise ValueError('fixed market must be 1825353')
 tmp=Path(tempfile.mkdtemp(prefix='eth_v73f_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V73F_SOURCE_ACTIVE_CHILD_FAMILY','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V73F_SOURCE_ACTIVE_CHILD_FAMILY_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};cr=by[a.market_id];models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{a.market_id}.json.xz'
  b=v70g.V70GGenerationScopedRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
  try:br=b.run_exam_v70g(models,cr['winner'])
  finally:b.close()
  c=V73FFamilyRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
  try:rr=c.run_exam_v73f(models,cr['winner'])
  finally:c.close()
  fam=rr.get('v73fResponsibilityFamilies',[]);child_fill=sum(float(x.get('qty') or 0.0) for x in rr.get('v53FillEvents',[]) if x.get('key') in set(rr.get('v73fChildKeys',[])) and x.get('role')=='ACTIVE_EXPAND')
  agg={'market':a.market_id,'baselineExpandDebt':float(br.get('v70dGenerationDebtQty') or 0.0),'candidateFamilyChildFillQty':child_fill,'candidateExpandFillQty':float(rr.get('v70dPhysicalExpandFillQty') or 0.0),'candidateGenerationDebtQty':float(rr.get('v70dGenerationDebtQty') or 0.0),'candidateGenerationPaidQty':float(rr.get('v70dGenerationPaidQty') or 0.0),'candidateGenerationRemainingQty':float(rr.get('v70dGenerationRemainingQty') or 0.0),'families':len(fam),'registeredFamilies':sum(bool(x.get('registeredForGenerationDebt')) for x in fam),'maxResponsibilitiesPerGeneration':int(rr.get('v70gMaxResponsibilitiesPerGeneration') or 0),'truthMismatch':float(rr.get('authorizedSubmitWithTruthRoleMismatch') or 0),'overOwned':float(rr.get('overOwnedSubmitViolations') or 0),'responsibilityOverfill':float(rr.get('v51ResponsibilityOverfill') or 0),'repairDrift':float(rr.get('repairToExpandAtFirstFill') or 0),'preBirthPaymentLeak':float(rr.get('v70dPreBirthPaymentLeak') or 0),'duplicateGenerationDebt':float(rr.get('v70dDuplicateGenerationDebt') or 0),'rounds':[int(br.get('v70dSemanticRounds') or 0),int(rr.get('v70dSemanticRounds') or 0)]}
  gates={'oneMarketCompletes':True,'activeFallbackChildObserved':child_fill>EPS,'childRegisteredSameResponsibility':agg['registeredFamilies']>=1,'childFillCreatesDebt':agg['candidateGenerationDebtQty']>EPS,'physicalFamilyFillEqualsGenerationDebt':abs(agg['candidateExpandFillQty']-agg['candidateGenerationDebtQty'])<=1e-7,'zeroDuplicateDebt':agg['duplicateGenerationDebt']<=EPS,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroPreBirthPaymentLeak':agg['preBirthPaymentLeak']<=EPS,'maxOneResponsibilityPerGeneration':agg['maxResponsibilitiesPerGeneration']<=1}
  decision='KEEP_V73F_SOURCE_ACTIVE_CHILD_FAMILY_ACCOUNTING_AUDIT_REPAIR_REACHABILITY' if all(gates.values()) else 'REJECT_V73F_FAMILY_HANDOFF'
  out={'version':'ETH_REPAIR_V73F_SOURCE_ACTIVE_CHILD_FAMILY_HFT_SMOKE','date':'2026-09-03','researchOnly':True,'behaviorChange':'accounting handoff only','fixedMarket':a.market_id,'aggregate':agg,'gates':gates,'decision':decision,'baselineV70G':br,'candidateV73F':rr,'boundary':['same V44/V65/V64 action chronology','source and active child share one Expand responsibility','actual child fill births debt once','no threshold/qty/price/delay tuning','no winner/PnL gate','no Stage-A/H100','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':agg,'gates':gates,'family':fam},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
