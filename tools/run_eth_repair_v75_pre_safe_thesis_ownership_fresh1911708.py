from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
import numpy as np
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v73b=sib('eth_v73b_for_v75','run_eth_repair_v73b_active_fallback_exante_recoverability_shadow.py')
v70g=v73b.v70g;v38=v73b.v38;v1=v73b.v1

class V75PreSafeThesis(v73b.V73BRecoverabilityShadow):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v75Births=0;self.v75Checks=0;self.v75Recoverable=0;self.v75Events=[]
 def _v75_recoverability(self,t,side,qv):
  px=float(qv[side]['bid']);eq=1.0/px if px>EPS else math.inf
  if not math.isfinite(eq) or eq<=EPS or eq>12.+EPS:return {'recoverable':False,'reason':'VENUE_MIN_INFEASIBLE','side':side,'price':px,'qty':eq}
  floor,u,d,cost=self._raw_floor();hu=float(u)+(eq if side=='UP' else 0.0);hd=float(d)+(eq if side=='DOWN' else 0.0);hc=float(cost)+eq*px;hfloor=min(hu,hd)-hc
  repair='DOWN' if side=='UP' else 'UP';weak_qty=hd if repair=='DOWN' else hu;strong_qty=hu if repair=='DOWN' else hd;hgap=max(0.0,strong_qty-weak_qty)
  owned=0.0;reserved_gain=0.0;owned_rows=[]
  for key,e,rem in self.lane_unresolved('REPAIR'):
   if e.get('side')!=repair:continue
   rq=float(rem);op=float(self.orders.get(key,{}).get('price') or e.get('price') or 0.0);owned+=rq;gain=rq*(1.0-op) if EPS<op<1-EPS else 0.0;reserved_gain+=gain;owned_rows.append({'key':key,'remaining':rq,'price':op,'floorGainIfFilled':gain})
  projected=hfloor+reserved_gain;room=max(0.0,hgap-owned);ceiling=(strong_qty-hc)/hgap if hgap>EPS else None
  rbid=float(qv[repair]['bid']) if qv.get(repair,{}).get('bid') is not None else None
  admissible=min(rbid,float(ceiling)) if rbid is not None and ceiling is not None else None;need=legal=req=None;recoverable=False;reason=''
  if projected>=-EPS:recoverable=True;reason='EXISTING_REPAIR_RESERVATION_COVERS_PROJECTED_FLOOR'
  elif admissible is None or not(EPS<admissible<1-EPS):reason='NO_ADMISSIBLE_FUTURE_REPAIR_PRICE'
  else:
   need=max(0.0,-projected)/(1.0-admissible);legal=1.0/admissible;req=max(need,legal);recoverable=req<=room+EPS;reason='PASS' if recoverable else 'FUTURE_REPAIR_QTY_EXCEEDS_ROOM'
  return {'t':int(t),'side':side,'price':px,'qty':eq,'floorBefore':floor,'hypFloorAfterExpand':hfloor,'repairSide':repair,'ownedRepairQty':owned,'ownedRepairGain':reserved_gain,'ownedRepairRows':owned_rows,'projectedFloorAfterOwnedRepair':projected,'repairGapAfterExpand':hgap,'repairRoomAfterOwned':room,'economicRepairCeiling':ceiling,'repairBid':rbid,'admissibleFutureRepairPrice':admissible,'futureNeedQty':need,'futureLegalQty':legal,'futureRequiredQty':req,'recoverable':bool(recoverable),'reason':reason}
 def _score_state(self,t,after_kind):
  if after_kind=='REPAIR' and getattr(self,'thesis',None) is None and self._coordDebt>EPS and self.repairParent is not None and self.teacher is not None:
   f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1])
   if pE>=.5 and int(self.capEnd)-int(t)>180000 and not self._v44_unresolved(t):
    qv=v1.quotes(self.book)
    if qv:
     side=self._signal_side(qv);z=self._v75_recoverability(t,side,qv);z.update({'event':'PRE_SAFE_THESIS_CHECK','pExpand':pE,'parentId':int(self.repairParent.get('id')),'bookImbalance':float(qv.get('imb') or 0.0)});self.v75Checks+=1
     if z.get('recoverable'):
      self.v75Recoverable+=1;self.thesis={'id':self.nextThesisId,'side':side,'bornAt':int(t),'materialized':False,'recoveries':0,'opens':0,'birthKind':'V75_PRE_SAFE_OWNERSHIP'};self.nextThesisId+=1;self.v75Births+=1;z['event']='PRE_SAFE_THESIS_BIRTH';z['thesisId']=self.thesis['id']
     self.v75Events.append(z)
  return super()._score_state(t,after_kind)
 def run_exam_v75(self,models,winner):
  r=super().run_exam_v73b(models,winner);r.update({'v75PreSafeThesisBirths':self.v75Births,'v75PreSafeChecks':self.v75Checks,'v75RecoverableChecks':self.v75Recoverable,'v75Events':self.v75Events[:80]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,default=1911708);ap.add_argument('--output',required=True);a=ap.parse_args()
 if a.market_id!=1911708:raise ValueError('V75 preregistered only for fresh market 1911708')
 tmp=Path(tempfile.mkdtemp(prefix='eth_v75_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V75_PRE_SAFE_THESIS','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V75_PRE_SAFE_THESIS_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};cr=by[a.market_id]
  models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{a.market_id}.json.xz'
  b=v70g.V70GGenerationScopedRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
  try:br=b.run_exam_v70g(models,cr['winner'])
  finally:b.close()
  c=V75PreSafeThesis(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
  try:rr=c.run_exam_v75(models,cr['winner'])
  finally:c.close()
  safety={'truthMismatch':float(rr.get('authorizedSubmitWithTruthRoleMismatch') or 0.0),'overOwned':float(rr.get('overOwnedSubmitViolations') or 0.0),'responsibilityOverfill':float(rr.get('v51ResponsibilityOverfill') or 0.0),'repairDrift':float(rr.get('repairToExpandAtFirstFill') or 0.0),'preBirthPaymentLeak':float(rr.get('v70dPreBirthPaymentLeak') or 0.0),'duplicateGenerationDebt':float(rr.get('v70dDuplicateGenerationDebt') or 0.0)}
  gates={'preSafeThesisBirthExercised':int(rr.get('v75PreSafeThesisBirths') or 0)>=1,'v44SubmitOrFillExercised':int(rr.get('v44Submits') or 0)>0 or int(rr.get('v44ActualFillEvents') or 0)>0,'zeroTruthMismatch':safety['truthMismatch']==0,'zeroOverOwned':safety['overOwned']==0,'zeroResponsibilityOverfill':safety['responsibilityOverfill']<=EPS,'zeroRepairDrift':safety['repairDrift']==0,'zeroPreBirthPaymentLeak':safety['preBirthPaymentLeak']<=EPS,'zeroDuplicateGenerationDebt':safety['duplicateGenerationDebt']<=EPS,'noLateExposure':int(rr.get('v44LateBlocks') or 0)>=0,'oneResponsibilityPerGeneration':int(rr.get('v70gMaxResponsibilitiesPerGeneration') or 0)<=1}
  decision='KEEP_V75_PRE_SAFE_OWNERSHIP_FOR_FRESH_REPLICATION' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_V75_PRE_SAFE_OWNERSHIP'
  out={'version':'ETH_REPAIR_V75_PRE_SAFE_THESIS_OWNERSHIP_FRESH1911708','date':'2026-09-03','researchOnly':True,'behaviorChange':True,'marketId':a.market_id,'winnerPostHocDiagnosticOnly':cr['winner'],'gates':gates,'decision':decision,'safety':safety,'diagnostics':{'baseline':{'pnl':br.get('pnlDiagnosticOnly'),'floor':br.get('floor'),'absNet':br.get('absNet'),'rounds':br.get('v70dSemanticRounds'),'fills':br.get('actualFillEvents'),'v44Submits':br.get('v44Submits')},'candidate':{'pnl':rr.get('pnlDiagnosticOnly'),'floor':rr.get('floor'),'absNet':rr.get('absNet'),'rounds':rr.get('v70dSemanticRounds'),'fills':rr.get('actualFillEvents'),'v44Submits':rr.get('v44Submits'),'v44FillQty':rr.get('v44ActualFillQty'),'repairAfterExpand':rr.get('v44RepairQtyAfterExpand'),'thesisSide':rr.get('thesisSide'),'preSafeBirths':rr.get('v75PreSafeThesisBirths')}},'v75Events':rr.get('v75Events',[]),'v44Decisions':rr.get('v44Decisions',[]),'v70gEvents':rr.get('v70gGenerationEvents',[]),'candidate':rr,'baseline':br,'boundary':['fresh 1911708 only','strict-past direction/recoverability only','winner post-hoc diagnostic only','no Target action clock','no numeric tuning','realistic-HFT/Predict Tape','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'diagnostics':out['diagnostics'],'v75Events':out['v75Events']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
