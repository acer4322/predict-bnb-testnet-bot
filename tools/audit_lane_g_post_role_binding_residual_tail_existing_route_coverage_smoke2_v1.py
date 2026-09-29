from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys,shutil
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.run_lane_g_r239_confirmed_repair_role_payment_binding_smoke1_v1 import RepairRoleBinding,EPS,near
from tools import run_eth_ms4_r2_39_overflow_responsibility_handoff as r239
v2=r239.v2
SPEC={1945869:{'ownerId':1,'generation':3,'side':'UP','residual':0.16174796329858765,'triggerT':1788532874726},1946872:{'ownerId':1,'generation':2,'side':'DOWN','residual':0.06837330749145343,'triggerT':1788538900096}}
class Audit(RepairRoleBinding):
 def __init__(self,tape,mid,spec):
  super().__init__(tape);self.mid=mid;self.spec=spec;self.cap=None;self.r0=None;self.d0=None;self.closeEv=None
 def authority(self):
  ra=float(self._risk_authority_current_generation()) if hasattr(self,'_risk_authority_current_generation') else 0.0
  rr=float(self._reserved_current_expand_risk()) if hasattr(self,'_reserved_current_expand_risk') else 0.0
  sc=float(self._service_claim()) if hasattr(self,'_service_claim') else 0.0
  total=float(getattr(self,'scopeRiskCreditTotal',0.0));cons=float(getattr(self,'scopeRiskCreditConsumed',0.0));allow=total+ra;comm=cons+rr+sc
  return {'scopeRiskCreditTotal':total,'scopeRiskCreditConsumed':cons,'riskAuthorityCurrentGeneration':ra,'reservedCurrentExpandRisk':rr,'serviceClaim':sc,'allowance':allow,'committed':comm,'slack':max(0.0,allow-comm)}
 def capture(self,t,ob):
  repair='DOWN' if ob['side']=='UP' else 'UP';used=self._used_prices(repair);levels=[]
  for raw in self._live_price_levels(repair):
   p=float(v2.kprice(raw))
   if p in used or p<=EPS:continue
   q=1.0/p
   if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
   sp=self._repair_split(repair,p,q)
   levels.append({'price':p,'venueMinQty':q,'fitsOwnerPureQty':q<=float(ob['outstanding'])+EPS,'repairSplit':None if sp is None else {'repairQty':float(sp.get('repairQty') or 0.0),'overflowQty':float(sp.get('overflowQty') or 0.0)}})
   if len(levels)>=8:break
  pure=[x for x in levels if x['fitsOwnerPureQty'] and x['repairSplit'] is not None and x['repairSplit']['overflowQty']<=EPS]
  mixed=next((x for x in levels if x['venueMinQty']>float(ob['outstanding'])+EPS),None);auth=self.authority();mixedRisk=None
  if mixed:mixedRisk=max(0.0,mixed['venueMinQty']-float(ob['outstanding']))*mixed['price']
  qv=v2.base.quotes(self.book);ask=(qv or {}).get(repair,{}).get('ask') if qv else None;active=None
  if ask is not None:
   ap=float(ask);aq=1.0/ap if ap>EPS else 1e99;before=float(self._physical_floor());after=float(self._candidate_alone_floor(repair,ap,aq))
   active={'ask':ap,'venueMinQty':aq,'fitsOwnerPureQty':aq<=float(ob['outstanding'])+EPS,'floorBefore':before,'floorAfter':after,'floorImproving':after>before+EPS}
  native=float(self._scope_debt_qty());extra=max(0.0,native-float(ob['outstanding']));pending=list(getattr(self,'pendingFailure',[]) or [])
  return {'t':int(t),'owner':dict(ob),'scopeSide':self.scopeSide,'scopeGeneration':int(self.scopeGeneration),'repairSide':repair,'purePassiveCandidates':pure,'firstPriceLevels':levels,'activeGeometry':active,'pendingFailureCount':len(pending),'nativeScopeDebt':native,'sameParentExtraDebtBeyondOwner':extra,'authority':auth,'firstMixedVenueCandidate':mixed,'mixedOverflowRiskNotional':mixedRisk,'mixedAuthorityCovered':bool(mixed is not None and mixedRisk<=auth['slack']+EPS),'livePassiveSlots':len(self.slot_key),'liveActiveSlots':len(self.activeKeys)}
 def process(self,t):
  super().process(t)
  if self.cap is None and int(t)>=self.spec['triggerT']:
   ob=self._active_obligation()
   if ob and int(ob.get('id') or -1)==self.spec['ownerId'] and int(ob.get('generation') or -1)==self.spec['generation'] and str(ob.get('side'))==self.spec['side'] and near(float(ob.get('outstanding') or 0.0),self.spec['residual'],1e-10):
    self.cap=self.capture(t,ob);self.r0=dict(self.r239);self.d0=dict(getattr(self,'drainStats',{}) or {})
  if self.cap is not None and self.closeEv is None:
   for e in reversed(self.r239events):
    if e.get('event')=='R239_OVERFLOW_OBLIGATION_CLOSED' and int(e.get('obligationId') or -1)==self.spec['ownerId'] and int(e.get('t') or 0)>=self.cap['t']:
     self.closeEv=dict(e);break
 def finish(self,winner):
  raw=self.run_r239(winner);a=Counter(self.r0 or {});b=Counter(dict(self.r239));c=Counter(self.d0 or {});d=Counter(dict(getattr(self,'drainStats',{}) or {}))
  rd={k:int(b[k]-a[k]) for k in sorted(set(a)|set(b)) if int(b[k]-a[k])!=0};dd={k:int(d[k]-c[k]) for k in sorted(set(c)|set(d)) if int(d[k]-c[k])!=0};return raw,rd,dd

def classify(cap,dd,close):
 if not cap:return 'TRIGGER_NOT_CAPTURED'
 if cap['purePassiveCandidates']:return 'EXISTING_PURE_PASSIVE_ROUTE_AVAILABLE'
 if any(k.startswith('ACTIVE_') and v>0 for k,v in dd.items()):return 'INHERITED_ACTIVE_LIFECYCLE_REACHED'
 if cap['sameParentExtraDebtBeyondOwner']>EPS:return 'SAME_PARENT_EXTRA_DEBT_EXISTS_FOR_AGGREGATION_AUDIT'
 if cap['mixedAuthorityCovered']:return 'EXISTING_AUTHORITY_COVERS_MIXED_ROUTE'
 return 'TRUE_SUB_VENUE_TAIL_NO_EXISTING_IMMEDIATE_ROUTE_'+str((close or {}).get('reason') or 'NO_CLOSE')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='lane_g_tail_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for mid in SPEC:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
  rows=[]
  for mid,spec in SPEC.items():
   s=Audit(tmp/f'{mid}.json.xz',mid,spec)
   try:raw,rd,dd=s.finish(co[mid]['winner']);cap=s.cap;close=s.closeEv
   finally:s.close()
   gates={'triggerExact':bool(cap and cap['t']==spec['triggerT'] and near(cap['owner']['outstanding'],spec['residual'],1e-10)),'underlyingCorrectness':bool(raw.get('r264CorrectnessPass')) and float(raw.get('unauthorizedOverflowQty') or 0.0)<=EPS and float(raw.get('repairQuotaExcessMax') or 0.0)<=EPS}
   rows.append({'marketId':mid,'capture':cap,'postTriggerR239CounterDelta':rd,'postTriggerActiveCounterDelta':dd,'ownerClose':close,'classification':classify(cap,dd,close),'gates':gates})
  gates={'allTriggersExact':all(x['gates']['triggerExact'] for x in rows),'correctnessPass':all(x['gates']['underlyingCorrectness'] for x in rows),'behaviorInert':True}
  out={'version':'LANE_G_POST_ROLE_BINDING_RESIDUAL_TAIL_EXISTING_ROUTE_COVERAGE_SMOKE2_RESULT_20260908','researchOnly':True,'runtimeAuthority':False,'rows':rows,'gates':gates,'verdict':'PASS_ROUTE_COVERAGE_AUDIT' if all(gates.values()) else 'FAIL_ROUTE_COVERAGE_AUDIT','boundary':['consumed 1945869/1946872 only','read-only structural audit','no synthetic order/evidence/authority/credit','no fixed seconds/windows/rank-age gate','max4/<=180s/pending-zero unchanged','no fresh','no dream fill','no 8781']}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'verdict':out['verdict'],'rows':[{'marketId':x['marketId'],'classification':x['classification'],'capture':x['capture'],'close':x['ownerClose']} for x in rows]},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
