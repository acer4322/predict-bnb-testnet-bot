from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v73b=sib('eth_v73b_for_v74b','run_eth_repair_v73b_active_fallback_exante_recoverability_shadow.py');v38=v73b.v38;v1=v73b.v1
FIXED=1911708
class V74BDustShadow(v73b.V73BRecoverabilityShadow):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v74bEvents=[];self.v74bDustActive=False;self.v74bDustQty=0.0;self.v74bDustSide=None;self.v74bOwnerBirths=0;self.v74bGrowthEvents=0;self.v74bRematerializableEvents=0;self.v74bLastSig=None
 def _live_repair_for(self,side):
  out=[]
  for k,e in self.carrierLedger.items():
   if str(e.get('objectiveRole') or '')!='REPAIR' or e.get('side')!=side or bool(e.get('terminalConfirmed')):continue
   rem=max(0.0,float(e.get('submittedQty') or 0)-float(e.get('actualFilled') or 0))
   if rem>EPS:out.append((k,rem))
  return out
 def _dust_shadow(self,t):
  rp=getattr(self,'repairParent',None)
  if rp is None or bool(rp.get('completed')):return
  side=rp.get('side')
  if side not in ('UP','DOWN'):return
  self._refresh_carrier_ledger_no_v70d()
  if self._live_repair_for(side):return
  pay=self._current_payoffs();gap=float(pay.get('gap') or 0.0)
  if gap<=EPS:return
  qv=v1.quotes(self.book)
  if not qv:return
  bid=qv.get(side,{}).get('bid');ask=qv.get(side,{}).get('ask')
  px=float(bid) if bid is not None else (float(ask) if ask is not None else None)
  if px is None or not(EPS<px<1-EPS):return
  notional=gap*px
  sig=(int(rp.get('id')),side,round(gap,9),round(px,4),bool(notional>=1-EPS))
  if notional<1-EPS:
   if not self.v74bDustActive:
    self.v74bDustActive=True;self.v74bDustQty=gap;self.v74bDustSide=side;self.v74bOwnerBirths+=1;ev='LATENT_DUST_OWNERSHIP'
   else:
    if gap>self.v74bDustQty+EPS:self.v74bGrowthEvents+=1;ev='LATENT_DUST_GROWTH'
    elif gap<self.v74bDustQty-EPS:ev='LATENT_DUST_REDUCED'
    else:ev='LATENT_DUST_STILL_PENDING'
    self.v74bDustQty=gap
   if sig!=self.v74bLastSig:self.v74bEvents.append({'t':int(t),'event':ev,'parentId':int(rp.get('id')),'side':side,'qty':gap,'price':px,'notional':notional,'owner':'DUST_REPAIR_PENDING','secondsLeft':(int(self.capEnd)-int(t))/1000.0});self.v74bLastSig=sig
  else:
   if self.v74bDustActive:
    self.v74bRematerializableEvents+=1;self.v74bEvents.append({'t':int(t),'event':'DUST_BECOMES_ADMISSIBLE_SHADOW','parentId':int(rp.get('id')),'side':side,'qty':gap,'price':px,'notional':notional,'secondsLeft':(int(self.capEnd)-int(t))/1000.0});self.v74bDustActive=False;self.v74bDustQty=gap
 def process(self,t):
  super().process(t);self._dust_shadow(t)
 def cancel_expired(self,t):
  super().cancel_expired(t);self._dust_shadow(t)
 def run_exam_v74b(self,models,winner):
  r=super().run_exam_v73b(models,winner);self._dust_shadow(int(self.capEnd));r.update({'v74bDustEvents':self.v74bEvents,'v74bDustOwnerBirths':self.v74bOwnerBirths,'v74bDustGrowthEvents':self.v74bGrowthEvents,'v74bDustRematerializableEvents':self.v74bRematerializableEvents,'v74bTerminalDustActive':self.v74bDustActive,'v74bTerminalDustQty':self.v74bDustQty,'v74bTerminalDustSide':self.v74bDustSide});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v74b_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V74B_FRESH_DUST_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V74B_FRESH_DUST_SHADOW_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];cr=next(r for r in co if int(r['marketId'])==FIXED);models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
  s=V74BDustShadow(tmp/'tapes'/f'{FIXED}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
  try:r=s.run_exam_v74b(models,cr['winner'])
  finally:s.close()
  safety=float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0)==0 and float(r.get('overOwnedSubmitViolations') or 0)==0 and float(r.get('v51ResponsibilityOverfill') or 0)<=EPS and float(r.get('repairToExpandAtFirstFill') or 0)==0 and float(r.get('v70dPreBirthPaymentLeak') or 0)<=EPS and float(r.get('v70dDuplicateGenerationDebt') or 0)<=EPS
  exact_submin_violation=any(float(x.get('qty') or 0)*float(x.get('price') or 0)<1-EPS for x in r.get('submitTrace',[]) if str(x.get('pendingRole'))=='REPAIR')
  parent_unresolved=any(not bool(x.get('completed')) for x in r.get('v34ParentLedger',[]))
  gates={'dustObserved':r['v74bDustOwnerBirths']>=1,'singleOwner':r['v74bDustOwnerBirths']==1,'noBelowMinRepairSubmit':not exact_submin_violation,'noOversizeOrFabricatedCompletion':r['v74bTerminalDustActive'] and parent_unresolved,'zeroSafetyAccountingChange':safety,'shadowOnly':True}
  decision='KEEP_V74_DUST_OWNER_FRESH_GAP_IS_NO_GROWTH_TO_ADMISSIBLE' if all(gates.values()) and r['v74bDustRematerializableEvents']==0 else ('KEEP_V74_DUST_OWNER_FRESH_REMATERIALIZATION_OPPORTUNITY_EXISTS' if all(gates.values()) else 'REJECT_V74B_DUST_LIFECYCLE_SHADOW')
  out={'version':'ETH_REPAIR_V74B_FRESH_1911708_DUST_LIFECYCLE_SHADOW','date':'2026-09-03','researchOnly':True,'behaviorChange':False,'fixedMarket':FIXED,'gates':gates,'decision':decision,'summary':{'terminalFloor':r.get('floor'),'terminalAbsNet':r.get('absNet'),'dustOwnerBirths':r['v74bDustOwnerBirths'],'dustGrowthEvents':r['v74bDustGrowthEvents'],'dustRematerializableEvents':r['v74bDustRematerializableEvents'],'terminalDustQty':r['v74bTerminalDustQty'],'terminalDustSide':r['v74bTerminalDustSide']},'events':r['v74bDustEvents'],'functional':r,'boundary':['fresh 1911708 only','shadow only','no action mutation','no winner/PnL gate','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'summary':out['summary'],'gates':gates,'events':r['v74bDustEvents']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
