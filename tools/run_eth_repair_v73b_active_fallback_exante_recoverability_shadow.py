from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v70g=sib('eth_v70g_for_v73b','run_eth_repair_v70g_generation_scoped_relay_hft_smoke.py');v65=v70g.v65;v38=v70g.v38;v1=v70g.v70f.v70d.v1
FIXED=[1823769,1827223,1827903]

class V73BRecoverabilityShadow(v70g.V70GGenerationScopedRelay):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v73bRows=[]
 def _shadow_active_recoverability(self,t,source_key,e,o,remaining):
  side=e.get('side') or o.get('side');repair='DOWN' if side=='UP' else 'UP' if side=='DOWN' else None;qv=v1.quotes(self.book)
  if side not in ('UP','DOWN') or repair is None or not qv or qv.get(side,{}).get('ask') is None:return None
  ask=float(qv[side]['ask']);legal_active=1.0/ask if ask>EPS else math.inf;eq=min(float(remaining),legal_active)
  if not math.isfinite(eq) or eq<=EPS or eq+EPS<legal_active:return None
  floor,u,d,cost=self._raw_floor();hu=float(u)+(eq if side=='UP' else 0.0);hd=float(d)+(eq if side=='DOWN' else 0.0);hc=float(cost)+eq*ask;hfloor=min(hu,hd)-hc
  weak_qty=hd if repair=='DOWN' else hu;strong_qty=hu if repair=='DOWN' else hd;hgap=max(0.0,strong_qty-weak_qty)
  owned=0.0;reserved_gain=0.0;owned_rows=[]
  for key,ce,rem in self.lane_unresolved('REPAIR'):
   if ce.get('side')!=repair:continue
   rq=float(rem);op=float(self.orders.get(key,{}).get('price') or ce.get('price') or 0.0);owned+=rq
   gain=rq*(1.0-op) if EPS<op<1-EPS else 0.0;reserved_gain+=gain;owned_rows.append({'key':key,'remaining':rq,'price':op,'floorGainIfFilled':gain})
  projected=hfloor+reserved_gain;room=max(0.0,hgap-owned);ceiling=(strong_qty-hc)/hgap if hgap>EPS else None
  rbid=float(qv.get(repair,{}).get('bid')) if qv.get(repair,{}).get('bid') is not None else None;rask=float(qv.get(repair,{}).get('ask')) if qv.get(repair,{}).get('ask') is not None else None
  admissible=min(rbid,float(ceiling)) if rbid is not None and ceiling is not None else None;need=legal_future=req=None;recoverable=False;reason=''
  if projected>=-EPS:
   recoverable=True;reason='EXISTING_REPAIR_RESERVATION_COVERS_PROJECTED_FLOOR'
  elif admissible is None or not(EPS<admissible<1-EPS):reason='NO_ADMISSIBLE_FUTURE_REPAIR_PRICE'
  else:
   need=max(0.0,-projected)/(1.0-admissible);legal_future=1.0/admissible;req=max(need,legal_future);recoverable=req<=room+EPS;reason='PASS' if recoverable else 'FUTURE_REPAIR_QTY_EXCEEDS_ROOM'
  row={'t':int(t),'sourceKey':source_key,'side':side,'activeAsk':ask,'activeQty':eq,'activeVenueMinQty':legal_active,'floorBefore':floor,'hypFloorAfterActiveExpand':hfloor,'repairSide':repair,'ownedRepairQty':owned,'ownedRepairGain':reserved_gain,'ownedRepairRows':owned_rows,'projectedFloorAfterOwnedRepair':projected,'repairGapAfterExpand':hgap,'repairRoomAfterOwned':room,'economicRepairCeiling':ceiling,'repairBid':rbid,'repairAsk':rask,'admissibleFuturePassiveRepairPrice':admissible,'futureNeedQty':need,'futureLegalQty':legal_future,'futureRequiredQty':req,'recoverable':bool(recoverable),'reason':reason}
  self.v73bRows.append(row);return row
 def _submit_active_expand(self,t,source_key,e,o,remaining):
  self._shadow_active_recoverability(t,source_key,e,o,remaining)
  return super()._submit_active_expand(t,source_key,e,o,remaining)
 def run_exam_v73b(self,models,winner):
  r=super().run_exam_v70g(models,winner);r.update({'v73bActiveFallbackRecoverabilityRows':self.v73bRows});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--v73-forward',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if mids!=FIXED:raise ValueError(f'fixed cohort must be {FIXED}, got {mids}')
 forward=json.load(open(a.v73_forward,encoding='utf-8'));labels={(int(x['marketId']),str(x['sourceKey'])):x for x in forward.get('branches',[]) if 'error' not in x}
 tmp=Path(tempfile.mkdtemp(prefix='eth_v73b_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V73B_EXANTE_RECOVERABILITY_SHADOW','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V73B_EXANTE_RECOVERABILITY_SHADOW_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in mids:
   cr=by[mid];s=V73BRecoverabilityShadow(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:r=s.run_exam_v73b(models,cr['winner'])
   finally:s.close()
   shadow=[]
   for z in r['v73bActiveFallbackRecoverabilityRows']:
    lab=labels.get((mid,str(z['sourceKey'])));shadow.append({**z,'postEpisodeForwardPairSum':lab.get('forwardPairSum') if lab else None,'postEpisodeFloorDeltaActiveThroughSettlement':lab.get('floorDeltaActiveThroughSettlement') if lab else None,'postEpisodeForwardNonDamaging':bool(lab and lab.get('forwardPairSum') is not None and float(lab['forwardPairSum'])<=1+EPS),'postEpisodeRecoveredFloorWindow':bool(lab and float(lab.get('floorDeltaActiveThroughSettlement') or 0)>0)})
   rows.append({'marketId':mid,'functional':r,'shadow':shadow});print(json.dumps({'marketId':mid,'activeFallbacks':len(shadow),'shadow':shadow,'safety':{'truthMismatch':r.get('authorizedSubmitWithTruthRoleMismatch'),'overOwned':r.get('overOwnedSubmitViolations'),'overfill':r.get('v51ResponsibilityOverfill')}},ensure_ascii=False),flush=True)
  allsh=[z for x in rows for z in x['shadow']];labeled=[z for z in allsh if z.get('postEpisodeForwardPairSum') is not None];recovered=[z for z in labeled if z.get('postEpisodeRecoveredFloorWindow') or z.get('postEpisodeForwardNonDamaging')]
  align=all(bool(z['recoverable']) for z in recovered) if recovered else False
  safety=all(float(x['functional'].get('authorizedSubmitWithTruthRoleMismatch') or 0)==0 and float(x['functional'].get('overOwnedSubmitViolations') or 0)==0 and float(x['functional'].get('v51ResponsibilityOverfill') or 0)<=EPS and float(x['functional'].get('repairToExpandAtFirstFill') or 0)==0 for x in rows)
  gates={'activeFallbackContextExercised':len(allsh)>=1,'allRecoveredBranchesRecoverableExAnte':align,'zeroSafetyAccountingChange':safety,'shadowOnlyNoActionMutation':True}
  decision='KEEP_EXANTE_RECOVERABILITY_AS_NEXT_FUNCTIONAL_AXIS' if all(gates.values()) else 'INCONCLUSIVE_NEED_RICHER_RECOVERABILITY_STATE'
  out={'version':'ETH_REPAIR_V73B_ACTIVE_FALLBACK_EXANTE_RECOVERABILITY_SHADOW','date':'2026-09-03','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'aggregate':{'markets':len(rows),'activeFallbackContexts':len(allsh),'recoverableContexts':sum(bool(z['recoverable']) for z in allsh),'labeledRecoveredBranches':len(recovered),'labeledRecoveredAndRecoverable':sum(bool(z['recoverable']) for z in recovered)},'gates':gates,'decision':decision,'rows':rows,'boundary':['shadow only','strict-past runtime state only','forward labels attached post-episode only','no action mutation','no winner/PnL gate','no H100','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':out['aggregate'],'gates':gates,'shadow':allsh},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
