from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.run_lane_g_satellite_overflow_r239_ownership_handoff_exact_v1 import SatelliteOverflowR239OwnershipHandoff
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
from tools.run_lane_g_shared_parent_parallel_residual_exact_fork_v1 import EPS,near
from tools import run_eth_ms4_r2_39_overflow_responsibility_handoff as r239
v2=r239.v2
MID=1946468; SOURCE='DOWN_13'; GEN=2

class OwnedSuccessorMixedVenueMinPreflight(SatelliteOverflowR239OwnershipHandoff):
 def __init__(self,tape,mid):
  super().__init__(tape,mid); self.capture=None; self.ownerOid=None
 def _try_overflow_handoff(self,t):
  ob=self._active_obligation()
  if self.capture is None and ob and SOURCE in (ob.get('sourceKeys') or []) and int(ob.get('generation') or -1)==GEN and self.scopeSide==ob.get('side') and int(self.scopeGeneration)==GEN:
   # Freeze only a state that inherited R239 can reach and where capacity is otherwise available.
   if self._live_handoff() is None and self._live_fanout_count()<self.fanoutLimit:
    repair_side='DOWN' if ob['side']=='UP' else 'UP'
    if len(self.slot_key)<self.max_slots and len(self._live_role_rows(side=repair_side))<self.max_slots:
     used=self._used_prices(repair_side); chosen=None
     for raw in self._live_price_levels(repair_side):
      p=float(v2.kprice(raw))
      if p in used or p<=EPS: continue
      q=1.0/p
      if not math.isfinite(q) or q<=EPS or q>12.0+EPS: continue
      if q>float(ob['outstanding'])+EPS:
       chosen=(p,q); break
     if chosen is not None:
      p,q=chosen; debt=float(ob['outstanding']); overflow=max(0.0,q-debt); overflow_notional=overflow*p
      allowance=float(self.scopeRiskCreditTotal)+float(self._risk_authority_current_generation())
      committed=float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk())+float(self._service_claim())
      slack=max(0.0,allowance-committed)
      cur_floor=float(self._physical_floor()); up=float(self.inv['UP']); dn=float(self.inv['DOWN']); cost=float(self.cost)+q*p
      if repair_side=='UP': up+=q
      else: dn+=q
      full_floor=min(up,dn)-cost
      led=GenerationAwareSharedParentDebtAllocationLedgerV3(); pid=900000+GEN; key='COUNTERFACTUAL_VENUE_MIN'
      led.register_carrier(key,pid,debt); alloc=led.allocate_cumulative(key,pid,q,debt)
      self.capture={'t':int(t),'obligationId':int(ob['id']),'sourceKeys':list(ob.get('sourceKeys') or []),'ownerSide':ob['side'],'generation':int(ob['generation']),'outstanding':debt,
       'repairSide':repair_side,'price':p,'venueMinQty':q,'repairFirstQty':float(alloc.repair_increment),'overflowQty':float(alloc.overflow_increment),'overflowNotional':overflow_notional,
       'currentFloor':cur_floor,'projectedFullCarrierFloor':full_floor,'floorDelta':full_floor-cur_floor,
       'slots':len(self.slot_key),'active':len(self.activeKeys),'fanoutLive':self._live_fanout_count(),
       'scopeRiskCreditTotal':float(self.scopeRiskCreditTotal),'scopeRiskCreditConsumed':float(self.scopeRiskCreditConsumed),'riskAuthorityCurrentGeneration':float(self._risk_authority_current_generation()),
       'reservedCurrentExpandRisk':float(self._reserved_current_expand_risk()),'serviceClaim':float(self._service_claim()),'combinedAuthorityAllowance':allowance,'combinedAuthorityCommitted':committed,'combinedAuthoritySlack':slack,
       'authorityCovered':overflow_notional<=slack+EPS,'floorNonWorse':full_floor>=cur_floor-EPS,
       'quantityConservation':near(float(alloc.repair_increment)+float(alloc.overflow_increment),q,2e-7),'repairDebtBounded':float(alloc.repair_increment)<=debt+EPS,
       'secondsLeft':(int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])-int(t))/1000.0}
  return super()._try_overflow_handoff(t)
 def run_preflight(self,winner):
  r=self.run_audit(winner); cap=self.capture
  birth=next((e for e in getattr(self,'r239events',[]) or [] if e.get('event')=='R239_OVERFLOW_OBLIGATION_BORN' and str(e.get('sourceKey'))==SOURCE and int(e.get('generation') or -1)==GEN),None)
  stats=dict(getattr(self,'r239',{}) or {})
  gates={'ownerBirthParity':bool(birth) and int(birth.get('t'))==1788534920674,'venueMinBlockReached':bool(cap) and int(stats.get('HANDOFF_LIABILITY_BELOW_VENUE_MIN',0))>0,
   'venueLegalMixedCandidate':bool(cap) and float(cap['venueMinQty'])>float(cap['outstanding'])+EPS,'r269Conservation':bool(cap) and cap['quantityConservation'] and cap['repairDebtBounded'],
   'existingAuthorityCoversOverflow':bool(cap) and cap['authorityCovered'],'fullCarrierFloorNonWorse':bool(cap) and cap['floorNonWorse'],
   'max4Preserved':bool(cap) and int(cap['slots'])+int(cap['active'])<4,'above180Boundary':bool(cap) and float(cap['secondsLeft'])>180.0,
   'underlyingCorrectnessPass':bool(r.get('r264CorrectnessPass')) and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS}
  return {'marketId':MID,'capture':cap,'r239Stats':stats,'ownerBirth':birth,'gates':gates,'preflightPass':all(gates.values())}

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); tmp=Path(tempfile.mkdtemp(prefix='lane_g_owned_mixed_preflight_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}; (tmp/f'{MID}.json.xz').write_bytes(z.read(f'tapes/{MID}.json.xz'))
  s=OwnedSuccessorMixedVenueMinPreflight(tmp/f'{MID}.json.xz',MID)
  try:r=s.run_preflight(co[MID]['winner'])
  finally:s.close()
  out={'version':'LANE_G_OWNED_SUCCESSOR_MIXED_VENUE_MIN_PREFLIGHT_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,'row':r,'boundary':['behavior-inert','first strict-past inherited venue-min blocker state','R269 counterfactual Repair-first allocation','no new authority','no fixed time/rank gate','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
  op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2),encoding='utf-8')
  print(json.dumps({'ok':True,'preflightPass':r['preflightPass'],'gates':r['gates'],'capture':r['capture']},ensure_ascii=False),flush=True)
 finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
