from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v65=sib('eth_v65_for_v70_reach','run_eth_repair_v65_global_expand_ownership_dedup_smoke.py')
v53=v65.v53;v38=v65.v38;v1=v65.v64.v1;EPS=1e-9

class V70CompositeReachability(v65.V65GlobalExpandDedup):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v70Reachability=[]
 def _submit_active_expand(self,t,source_key,e,o,remaining):
  self._refresh_carrier_ledger(t)
  side=e.get('side') or o.get('side');rp=getattr(self,'repairParent',None);pid=None if rp is None else int(rp.get('id'));rside=None if rp is None else rp.get('side')
  try:pay=self._current_payoffs()
  except Exception:pay={'gap':0.0,'floor':None}
  gap=max(0.0,float(pay.get('gap') or 0.0));same=bool(side in ('UP','DOWN') and side==rside);repair_alloc=min(float(remaining),gap) if same else 0.0;overflow=max(0.0,float(remaining)-repair_alloc)
  qv=v1.quotes(self.book);ask=None if side not in ('UP','DOWN') or not qv or not qv.get(side) else qv[side].get('ask');legal=math.inf if ask is None or float(ask)<=EPS else 1.0/float(ask)
  psv=None if pid is None else self._find_passive(pid);pk=None;plive=False;prem=0.0;pterminal=None
  if psv is not None:
   pk,pe,prem,po=psv
   try:plive=bool(v1.live(self.snap(po).get('status')))
   except Exception:plive=False
   pterminal=bool(pe.get('terminalConfirmed'))
  dual=bool(same and repair_alloc>EPS and overflow>EPS)
  row={'t':int(t),'sourceKey':source_key,'sourceObjectiveId':e.get('objectiveId') or o.get('objective_id'),'sourceSide':side,'sourceRemainingQty':float(remaining),'sourceTerminal':bool(e.get('terminalConfirmed')),'sourceActualFilled':float(e.get('actualFilled') or 0.0),'remainingSec':(int(self.capEnd)-int(t))/1000.0,'repairParentId':pid,'repairSide':rside,'repairGap':gap,'sameSideRepair':same,'repairAuthorizedQty':repair_alloc,'expandOverflowQty':overflow,'dualResponsibilityReachable':dual,'venueAsk':None if ask is None else float(ask),'venueLegalMinQty':None if not math.isfinite(legal) else legal,'compositeVenueLegal':bool(math.isfinite(legal) and float(remaining)+EPS>=legal),'repairAloneVenueLegal':bool(math.isfinite(legal) and repair_alloc+EPS>=legal),'rescuesSubminimumRepair':bool(dual and repair_alloc+EPS<legal and float(remaining)+EPS>=legal),'repairCarrierKey':pk,'repairCarrierRemaining':float(prem),'repairCarrierLive':plive,'repairCarrierTerminal':pterminal,'readyWithoutRepairHandoff':bool(dual and not plive and math.isfinite(legal) and float(remaining)+EPS>=legal and int(self.capEnd)-int(t)>180000),'requiresRepairCarrierHandoff':bool(dual and plive),'pre180ExposureAllowed':bool(int(self.capEnd)-int(t)>180000)}
  self.v70Reachability.append(row)
  return super()._submit_active_expand(t,source_key,e,o,remaining)
 def run_exam_v70(self,models,winner):
  r=super().run_exam_v65(models,winner);r.update({'v70CompositeReachability':self.v70Reachability});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v70_reach_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V70_REACHABILITY','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V70_REACHABILITY_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];c=V70CompositeReachability(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:r=c.run_exam_v70(models,cr['winner'])
   finally:c.close()
   z=r['v70CompositeReachability'];rows.append({'marketId':mid,'candidate':r,'reachability':z});print(json.dumps({'marketId':mid,'fallbackClocks':len(z),'dual':sum(x['dualResponsibilityReachable'] for x in z),'ready':sum(x['readyWithoutRepairHandoff'] for x in z),'handoff':sum(x['requiresRepairCarrierHandoff'] for x in z),'sameSide':sum(x['sameSideRepair'] for x in z),'supersedeBlocks':r['v65GlobalSupersedeBlocks']},ensure_ascii=False),flush=True)
  z=[q for x in rows for q in x['reachability']]
  agg={'markets':len(rows),'fallbackClocksCovered':len(z),'marketsWithFallbackClock':sum(bool(x['reachability']) for x in rows),'globalSupersedeBlocks':sum(int(x['candidate']['v65GlobalSupersedeBlocks']) for x in rows),'sameSideRepairAtFallback':sum(q['sameSideRepair'] for q in z),'dualResponsibilityReachable':sum(q['dualResponsibilityReachable'] for q in z),'readyWithoutRepairHandoff':sum(q['readyWithoutRepairHandoff'] for q in z),'requiresRepairCarrierHandoff':sum(q['requiresRepairCarrierHandoff'] for q in z),'rescuesSubminimumRepair':sum(q['rescuesSubminimumRepair'] for q in z),'sideMismatch':sum(q['repairSide'] is not None and not q['sameSideRepair'] for q in z),'noRepairResponsibility':sum(q['repairParentId'] is None or q['repairGap']<=EPS for q in z),'noExpandOverflowAfterRepair':sum(q['sameSideRepair'] and q['expandOverflowQty']<=EPS for q in z),'truthMismatch':sum(float(x['candidate'].get('authorizedSubmitWithTruthRoleMismatch') or 0) for x in rows),'overOwned':sum(float(x['candidate'].get('overOwnedSubmitViolations') or 0) for x in rows),'repairDrift':sum(float(x['candidate'].get('repairToExpandAtFirstFill') or 0) for x in rows),'responsibilityOverfill':sum(float(x['candidate'].get('v51ResponsibilityOverfill') or 0) for x in rows)}
  gates={'allThreeMarketsCompleted':agg['markets']==3,'existingV65FallbackClockCovered':agg['fallbackClocksCovered']>0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS}
  if agg['dualResponsibilityReachable']>0:decision='PROCEED_ONLY_ON_OBSERVED_REACHABLE_MARKET_TO_COMPOSITE_FUNCTIONAL_SMOKE'
  else:decision='REJECT_V64_FALLBACK_CLOCK_AS_COMPOSITE_BIND_POINT_USE_OUR_REPAIR_DECISION_PARENT_COUNTERFACTUAL_NEXT'
  out={'version':'ETH_REPAIR_V70_COMPOSITE_CARRIER_REACHABILITY_SMOKE','researchOnly':True,'behaviorChangeRelativeToV65':False,'actionAuthority':False,'aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'decision':decision,'rows':rows,'boundary':['Instrumentation is attached only to already-authorized V65 active Expand fallback calls.','V65 behavior remains unchanged; no composite submit is made.','Dual responsibility requires the current Repair side to equal the authorized failed V44 Expand side and both allocations to be positive.','A live Repair carrier is recorded as a required ownership handoff, never overlapped.','No Target clock, winner/PnL trigger, threshold/qty/delay tuning, dream fill, H100, or 8781.']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'decision':decision,'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
