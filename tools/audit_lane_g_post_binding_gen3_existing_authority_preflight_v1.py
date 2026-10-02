from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.run_lane_g_r239_confirmed_payment_binding_exact_fork_v1 import R239ConfirmedPaymentBindingFork,MID,EXPECTED_OVERFLOW,near,EPS
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
from tools import run_eth_ms4_r2_39_overflow_responsibility_handoff as r239
v2=r239.v2

class Gen3AuthorityPreflight(R239ConfirmedPaymentBindingFork):
 def __init__(self,tape,mid):
  super().__init__(tape,mid,'R239_BIND_CONFIRMED_NATIVE_REPAIR_BEFORE_SCOPE_FLIP');self.capture=None
 def _try_overflow_handoff(self,t):
  ob=self._active_obligation()
  if self.capture is None and ob and int(ob.get('id') or -1)==2 and int(ob.get('generation') or -1)==3 and str(ob.get('side'))=='UP' and 'UP_20' in (ob.get('sourceKeys') or []) and self.scopeSide=='UP' and int(self.scopeGeneration)==3:
   if self._live_handoff() is None and self._live_fanout_count()<self.fanoutLimit:
    repair='DOWN'
    if len(self.slot_key)<self.max_slots and len(self._live_role_rows(side=repair))<self.max_slots:
     used=self._used_prices(repair);chosen=None
     for raw in self._live_price_levels(repair):
      p=float(v2.kprice(raw))
      if p in used or p<=EPS:continue
      q=1.0/p
      if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
      if q>float(ob['outstanding'])+EPS:chosen=(p,q);break
     if chosen:
      p,q=chosen;debt=float(ob['outstanding']);led=GenerationAwareSharedParentDebtAllocationLedgerV3();pid=990003;key='GEN3_PREFLIGHT';led.register_carrier(key,pid,debt);a=led.allocate_cumulative(key,pid,q,debt)
      overflow=float(a.overflow_increment);risk=overflow*p
      allowance=float(self.scopeRiskCreditTotal)+float(self._risk_authority_current_generation())
      committed=float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk())+float(self._service_claim())
      slack=max(0.0,allowance-committed)
      end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
      self.capture={'t':int(t),'ownerId':2,'ownerSide':'UP','generation':3,'outstanding':debt,'repairSide':repair,'price':p,'venueMinQty':q,
       'repairFirstQty':float(a.repair_increment),'overflowQty':overflow,'overflowNotional':risk,
       'slots':len(self.slot_key),'active':len(self.activeKeys),'fanoutLive':self._live_fanout_count(),
       'scopeRiskCreditTotal':float(self.scopeRiskCreditTotal),'scopeRiskCreditConsumed':float(self.scopeRiskCreditConsumed),
       'riskAuthorityCurrentGeneration':float(self._risk_authority_current_generation()),'reservedCurrentExpandRisk':float(self._reserved_current_expand_risk()),'serviceClaim':float(self._service_claim()),
       'combinedAuthorityAllowance':allowance,'combinedAuthorityCommitted':committed,'combinedAuthoritySlack':slack,'authorityCovered':risk<=slack+EPS,
       'quantityConservation':near(float(a.repair_increment)+overflow,q,2e-7),'repairDebtBounded':float(a.repair_increment)<=debt+EPS,'secondsLeft':(end-int(t))/1000.0}
  return super()._try_overflow_handoff(t)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_gen3_auth_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};(tmp/f'{MID}.json.xz').write_bytes(z.read(f'tapes/{MID}.json.xz'))
  s=Gen3AuthorityPreflight(tmp/f'{MID}.json.xz',MID)
  try:r=s.run_payment(co[MID]['winner']);cap=s.capture
  finally:s.close()
  gates={'paymentBindingUpstreamPass':bool(r.get('paymentEvent')) and not r.get('paymentErrors'),'owner2Exact':bool(cap) and near(float(cap.get('outstanding') or 0.0),EXPECTED_OVERFLOW,1e-12),
   'venueMinMixedCandidateExists':bool(cap) and float(cap['venueMinQty'])>float(cap['outstanding'])+EPS,'r269Conservation':bool(cap) and cap['quantityConservation'] and cap['repairDebtBounded'],
   'existingAuthorityCoversOverflow':bool(cap) and cap['authorityCovered'],'max4Preserved':bool(cap) and int(cap['slots'])+int(cap['active'])<4,'above180Boundary':bool(cap) and float(cap['secondsLeft'])>180.0,
   'underlyingCorrectnessPass':bool(r['upstreamMixed']['correct']) and float(r.get('combinedAuthorityExcessMax') or 0.0)<=EPS and float(r.get('nativeUnauthorizedOverflowQty') or 0.0)<=EPS}
  out={'version':'LANE_G_POST_BINDING_GEN3_EXISTING_AUTHORITY_PREFLIGHT_V1_RESULT_20260907','researchOnly':True,'behaviorMutation':False,'marketId':MID,'capture':cap,'gates':gates,'preflightPass':all(gates.values()),
       'classification':('GEN3_ONE_MORE_MIXED_CARRIER_AUTHORITY_COVERED' if all(gates.values()) else ('GEN3_RECURSIVE_MIXED_AUTHORITY_NEGATIVE_STOP' if cap and not cap['authorityCovered'] else 'GEN3_PREFLIGHT_OTHER_FAIL')),
       'boundary':['read-only preflight','no counterfactual submit','existing authority only','consumed 1946468','realistic HFT','fresh untouched','no dream fill','no 8781','no fixed time/rank-age gate']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
