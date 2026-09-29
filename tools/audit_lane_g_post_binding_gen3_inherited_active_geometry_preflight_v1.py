from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.run_lane_g_r239_confirmed_payment_binding_exact_fork_v1 import R239ConfirmedPaymentBindingFork,MID,EXPECTED_OVERFLOW,near,EPS
from tools.run_eth_ms4_r2_2_failure_evidence_active_drain import r1
class A(R239ConfirmedPaymentBindingFork):
 def __init__(self,tape,mid):super().__init__(tape,mid,'R239_BIND_CONFIRMED_NATIVE_REPAIR_BEFORE_SCOPE_FLIP');self.cap=None
 def _register_overflow(self,t,ev):
  before=int(getattr(self,'_nextObligationId',0));super()._register_overflow(t,ev)
  if self.cap is None and before==2 and int(getattr(self,'_nextObligationId',0))==3:
   ob=next((dict(x) for x in self.obligations if int(x.get('id') or -1)==2),None)
   if ob:
    repair='DOWN';qv=r1.v2.base.quotes(self.book);ask=None if not qv else qv.get(repair,{}).get('ask');q=(math.inf if ask is None or float(ask)<=EPS else 1.0/float(ask));debt=float(ob['outstanding']);reserved=float(self._reserved_repair_quota(repair));avail=max(0.0,debt-reserved);beforef=float(self._physical_floor());after=None if not math.isfinite(q) else float(self._candidate_alone_floor(repair,float(ask),q))
    self.cap={'t':int(t),'owner2':ob,'repairSide':repair,'activeAsk':None if ask is None else float(ask),'venueMinQty':q,'scopeDebt':float(self._scope_debt_qty()),'reservedRepairQuota':reserved,'availableRepairDebt':avail,'debtCoversVenueMin':bool(math.isfinite(q) and avail+EPS>=q),'candidateFloor':after,'physicalFloor':beforef,'floorImproving':bool(after is not None and after>beforef+EPS),'liveActive':bool(self._has_live_active()),'slots':len(self.slot_key),'activeKeys':len(self.activeKeys)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_gen3_active_pre_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};(tmp/f'{MID}.json.xz').write_bytes(z.read(f'tapes/{MID}.json.xz'))
  s=A(tmp/f'{MID}.json.xz',MID)
  try:r=s.run_payment(co[MID]['winner']);c=s.cap
  finally:s.close()
  cls='GEN3_ACTIVE_PURE_REPAIR_VENUE_MIN_INCOMPATIBLE' if c and not c['debtCoversVenueMin'] else 'GEN3_ACTIVE_GEOMETRY_OTHER'
  gates={'owner2Exact':bool(c) and near(float(c['owner2']['outstanding']),EXPECTED_OVERFLOW,1e-12),'noLiveActiveAtBirth':bool(c) and not c['liveActive'],'venueMinGeometryObserved':bool(c) and c['activeAsk'] is not None and math.isfinite(c['venueMinQty']),'existingPureActiveDebtCoverage':bool(c) and c['debtCoversVenueMin'],'paymentBindingUpstreamPass':bool(r.get('paymentEvent')) and not r.get('paymentErrors')}
  out={'version':'LANE_G_POST_BINDING_GEN3_INHERITED_ACTIVE_GEOMETRY_PREFLIGHT_V1_RESULT_20260907','researchOnly':True,'behaviorMutation':False,'marketId':MID,'capture':c,'gates':gates,'classification':cls,'boundary':['read-only exact owner2-birth preflight','existing inherited Active pure-Repair semantics only','no synthetic failure evidence','no submit','no new authority/credit','consumed 1946468','realistic HFT','fresh untouched','no dream fill','no 8781','no fixed-time/rank-age gate']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'classification':cls,'capture':c,'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
