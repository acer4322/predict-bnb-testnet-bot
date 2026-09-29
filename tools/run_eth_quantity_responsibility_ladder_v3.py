"""Research-only V3: exact-quantity responsibility ledger + existing Repair execution ladder.

Key change from V1/V2: manager responsibility is an exact FIFO quantity lot, not an event token.
A carrier is only one payment attempt. A fill never completes the responsibility unless remainingQty
actually reaches zero. Small residuals below the venue's structural minimum legal tranche remain in
ledger but do not authorize a dedicated enhanced Passive/Active ladder; ordinary concurrent Pair-only
activity may still pay them. No PnL/Floor/winner threshold is action authority.
"""
from __future__ import annotations
import argparse,collections,importlib.util,json,math,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_quantity_responsibility_ledger_shadow_v1.py'
if _STAGED.exists():
 spec=importlib.util.spec_from_file_location('staged_qty_shadow',_STAGED);qshadow=importlib.util.module_from_spec(spec);spec.loader.exec_module(qshadow)
else:
 import tools.run_eth_quantity_responsibility_ledger_shadow_v1 as qshadow
v1=qshadow.v1;base=v1.base
EPS=1e-9;TICK=0.01;ACTIVE_WINDOW_MS=500;REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class QuantityResponsibilityLadderV3(qshadow.QuantityLedgerShadowSim):
 def __init__(self,tape):
  # V1 generation ladder disabled. Exact quantity ledger is the only manager ledger used by V3.
  super().__init__(tape,False)
  self.qty_enabled=True;self.q_arm=None;self.q_ladder=None;self.q_pending_active=None;self.q_active_remainder_cancel=False
  self.q_events=[];self.q_counter=collections.Counter();self.q_managed_repair_qty=0.0;self.q_managed_overflow_qty=0.0;self._inside_parent_process=False
 def _lot_by_id(self,rid):
  for x in self.resp_all:
   if int(x['id'])==int(rid):return x
  return None
 def _oldest_for_repair_side(self,repair_side):
  expand_side=qshadow.opp(repair_side);dq=self.resp_queues[expand_side]
  return dq[0] if dq else None
 def _lot_remaining(self,rid):
  x=self._lot_by_id(rid);return float(x['remainingQty']) if x is not None else 0.0
 def process(self,t):
  before=len(self.fill_accounting);self._inside_parent_process=True
  try:super().process(t)
  finally:self._inside_parent_process=False
  new=self.fill_accounting[before:]
  L=self.q_ladder
  if L:
   for a in new:
    if a['key'] in {L.get('passiveKey'),L.get('activeKey')}:
     L['carrierFillQty']=float(L.get('carrierFillQty',0.0))+float(a['confirmedQty'])
     L['carrierRepairQty']=float(L.get('carrierRepairQty',0.0))+float(a['matchedRepairQty'])
     L['carrierOverflowQty']=float(L.get('carrierOverflowQty',0.0))+float(a['overflowQty'])
     self.q_managed_repair_qty+=float(a['matchedRepairQty']);self.q_managed_overflow_qty+=float(a['overflowQty'])
     self.q_events.append({'event':'QTY_MANAGED_FILL','t':int(t),'responsibilityId':L['responsibilityId'],'route':L.get('route'),'key':a['key'],'confirmedQty':a['confirmedQty'],'fifoRepairQtyDiagnostic':a['matchedRepairQty'],'overflowQtyDiagnostic':a['overflowQty'],'targetRemainingAfterClock':self._lot_remaining(L['responsibilityId']),'executionPrice':a['executionPriceFromInheritedSubstrate']})
   # If some other concurrent fill fully paid the target, this carrier loses ownership and is canceled.
   if self._lot_remaining(L['responsibilityId'])<=EPS and L.get('route')=='PASSIVE':
    key=L.get('passiveKey');o=self.orders.get(key)
    if o is not None and float(o.get('cum') or 0.0)<=EPS and not o.get('cancelRequested'):
     sid=next((s for s,k in self.slot_key.items() if k==key),None)
     if sid is not None and super()._request_cancel(t,int(sid),'QTY_RESPONSIBILITY_SATISFIED_ELSEWHERE'):
      self.q_counter['responsibilitySatisfiedElsewhereCancels']+=1;L['satisfiedElsewhere']=True
      self.q_events.append({'event':'QTY_RESPONSIBILITY_SATISFIED_ELSEWHERE_CANCEL','t':int(t),'responsibilityId':L['responsibilityId'],'key':key})
  self._manage_active_remainder(t)
 def _arm_for_open_qty(self,t,qv):
  if not self.qty_enabled or self.q_ladder is not None or self.q_pending_active is not None:return None
  side,role,_,_=base.MinimalPairRoleSim._role_decision(self,qv)
  if role not in REPAIR_ROLES:return None
  lot=self._oldest_for_repair_side(side)
  if lot is None or float(lot['remainingQty'])<=EPS:return None
  return {'t':int(t),'side':side,'role':role,'responsibilityId':int(lot['id']),'remainingQty':float(lot['remainingQty']),'bornAt':int(lot['bornAt']),'expandSide':lot['side'],'expandPrice':float(lot['price']),'qv':qv}
 def _open_one_option(self,t,qv,end):
  if self.q_pending_active is not None:
   if int(end)-int(t)<=base.v2.NO_NEW_EXPOSURE_MS:
    self._complete_carrier(t,'ACTIVE_BLOCKED_LATE_180S');self.q_pending_active=None
   elif self._submit_protected_active_qty(t,qv):
    self.q_pending_active=None;return
  self.q_arm=self._arm_for_open_qty(t,qv)
  try:return super()._open_one_option(t,qv,end)
  finally:self.q_arm=None
 def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
  original=super()._candidate_from_levels(side,require_pair,require_budget);a=self.q_arm
  if original is None or a is None or side!=a['side']:return original
  p0,q0,proj=original;qv=a['qv'];bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);price=round(ask-TICK,10)
  used={round(float(x),10) for x in self._used_prices(side)}
  if not (EPS<bid<price<ask-EPS and price not in used and price>float(p0)+EPS):self.q_counter['noInsideSpreadImprovement']+=1;return original
  qty=1.0/price
  # Structural venue feasibility, not a tuned economic threshold: dedicated ladder cannot represent a debt smaller than minimum legal tranche.
  remaining=self._lot_remaining(a['responsibilityId'])
  if remaining+EPS<qty:
   self.q_counter['debtBelowDedicatedMinimum']+=1
   self.q_events.append({'event':'QTY_DEBT_DEFERRED_BELOW_MINIMUM','t':int(a['t']),'responsibilityId':a['responsibilityId'],'remainingQty':remaining,'minimumCarrierQty':qty,'side':side})
   return original
  if qty>12.0+EPS:self.q_counter['managedQtyAbove12Fallback']+=1;return original
  a.update({'inheritedPrice':float(p0),'inheritedQty':float(q0),'passivePrice':float(price),'passiveQty':float(qty),'bidAtDecision':bid,'askAtDecision':ask,'remainingAtSubmitDecision':remaining,'passivePairSum':float(a['expandPrice']+price)})
  return float(price),float(qty),proj
 def _submit_role(self,t,side,role,p,q,proj,source):
  before_n=self.n;ok=super()._submit_role(t,side,role,p,q,proj,source);a=self.q_arm
  if ok and self.q_ladder is None and a is not None and 'passivePrice' in a and side==a['side'] and role==a['role'] and abs(float(p)-float(a['passivePrice']))<=EPS:
   key=f'{side}_{before_n}';self.q_ladder={'responsibilityId':a['responsibilityId'],'side':side,'role':role,'route':'PASSIVE','passiveKey':key,'passiveSubmittedAt':int(t),'passivePrice':float(p),'passiveQty':float(q),'inheritedPrice':a['inheritedPrice'],'targetRemainingAtSubmit':a['remainingAtSubmitDecision'],'carrierFillQty':0.0,'carrierRepairQty':0.0,'carrierOverflowQty':0.0,'priorityLossAt':None,'activeKey':None,'satisfiedElsewhere':False}
   self.q_counter['managedPassiveSubmits']+=1;self.q_events.append({'event':'QTY_MANAGED_PASSIVE_SUBMIT','t':int(t),'responsibilityId':a['responsibilityId'],'side':side,'role':role,'key':key,'price':float(p),'qty':float(q),'targetRemaining':a['remainingAtSubmitDecision']})
  return ok
 def _request_cancel(self,t,sid,reason):
  key=self.slot_key.get(int(sid));L=self.q_ladder
  if L and L.get('route')=='PASSIVE' and key==L.get('passiveKey') and reason in {'CORE_INVALIDATED','SATELLITE_FRONTIER_REANCHOR'}:
   o=self.orders.get(key)
   if o is not None and int(t)-int(o['placed'])<int(base.v2.base.TTL) and L.get('priorityLossAt') is None:
    self.q_counter['ordinaryCancelSuppressed']+=1;return False
  return super()._request_cancel(t,sid,reason)
 def _reanchor_stale(self,t):
  L=self.q_ladder
  if L and L.get('route')=='PASSIVE' and L.get('priorityLossAt') is None and not L.get('satisfiedElsewhere'):
   key=L.get('passiveKey');o=self.orders.get(key)
   if o is not None and not o.get('cancelRequested') and float(o.get('cum') or 0.0)<=EPS:
    qv=base.v2.base.quotes(self.book)
    if qv:
     bid=float(qv[L['side']]['bid']);ask=float(qv[L['side']]['ask']);own=float(o['price'])
     if bid>own+EPS:
      sid=next((s for s,k in self.slot_key.items() if k==key),None)
      if sid is not None and super()._request_cancel(t,int(sid),'QTY_MANAGED_PRIORITY_LOSS'):
       L['priorityLossAt']=int(t);self.q_counter['priorityLossCancels']+=1;self.q_events.append({'event':'QTY_MANAGED_PRIORITY_LOSS_CANCEL','t':int(t),'responsibilityId':L['responsibilityId'],'key':key,'ownPrice':own,'bestBid':bid,'bestAsk':ask})
  return super()._reanchor_stale(t)
 def _refresh_slots(self,t):
  start=len(self.slot_history);super()._refresh_slots(t);releases=[x for x in self.slot_history[start:] if x.get('event')=='SLOT_RELEASE'];L=self.q_ladder
  if not L:return
  for e in releases:
   key=e.get('key');status=str(e.get('status') or '').upper();cum=float(e.get('cum') or 0.0);target=self._lot_remaining(L['responsibilityId'])
   if L.get('route')=='PASSIVE' and key==L.get('passiveKey'):
    self.q_events.append({'event':'QTY_MANAGED_PASSIVE_TERMINAL','t':int(t),'responsibilityId':L['responsibilityId'],'key':key,'status':status,'cum':cum,'targetRemaining':target})
    if cum>EPS or status=='FILLED':
     self.q_counter['managedPassivePhysicalSuccess']+=1;self._complete_carrier(t,'PASSIVE_PROGRESS')
    elif target<=EPS or L.get('satisfiedElsewhere'):
     self.q_counter['managedPassiveTargetSatisfiedWithoutFill']+=1;self._complete_carrier(t,'TARGET_SATISFIED_ELSEWHERE')
    else:
     o=self.orders.get(key);remaining=max(0.0,float(o.get('qty') or L.get('passiveQty') or 0)-float(o.get('cum') or 0)) if o else float(L.get('passiveQty') or 0)
     self.q_pending_active={'responsibilityId':L['responsibilityId'],'side':L['side'],'role':L['role'],'sourceKey':key,'sourceRemainingQty':remaining,'sourceTerminalAt':int(t)};L['route']='PENDING_ACTIVE';self.q_counter['managedPassiveZeroFillTerminal']+=1
   elif L.get('route')=='ACTIVE' and key==L.get('activeKey'):
    self.q_events.append({'event':'QTY_MANAGED_ACTIVE_TERMINAL','t':int(t),'responsibilityId':L['responsibilityId'],'key':key,'status':status,'cum':cum,'targetRemaining':target})
    if cum>EPS:self.q_counter['managedActivePhysicalSuccess']+=1
    else:self.q_counter['managedActiveZeroFillTerminal']+=1
    self._complete_carrier(t,'ACTIVE_TERMINAL')
  self._manage_active_remainder(t)
 def _submit_protected_active_qty(self,t,qv):
  pnd=self.q_pending_active;L=self.q_ladder
  if pnd is None or L is None:return False
  rid=int(pnd['responsibilityId']);target=self._lot_remaining(rid)
  if target<=EPS:self.q_counter['activeSuppressedTargetSatisfied']+=1;self._complete_carrier(t,'TARGET_SATISFIED_BEFORE_ACTIVE');return False
  side=pnd['side'];role=pnd['role'];ask=float(qv[side]['ask']);bid=float(qv[side]['bid']);limit=round(min(.99,ask+TICK),10);min_qty=1.0/limit
  source_remaining=float(pnd['sourceRemainingQty']);qty=min(source_remaining,target)
  if qty+EPS<min_qty:
   self.q_counter['activeDeferredBelowMinimum']+=1;self.q_events.append({'event':'QTY_ACTIVE_DEFERRED_BELOW_MINIMUM','t':int(t),'responsibilityId':rid,'targetRemaining':target,'sourceRemainingQty':source_remaining,'minimumActiveQty':min_qty});self._complete_carrier(t,'ACTIVE_DEFERRED_BELOW_MINIMUM');return False
  if len(self.slot_key)>=self.max_slots:self.q_counter['activeNoFreeSlot']+=1;return False
  free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
  if free is None:self.q_counter['activeNoFreeSlot']+=1;return False
  n=self.n;self.n+=1;ex=base.v2.base.ex;native_side,native_price=ex.native_order(side,limit)
  try:
   if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
   else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
  except Exception:self.q_counter['activeSubmitException']+=1;return False
  key=f'{side}_{n}';self.orders[key]={'n':n,'side':side,'price':limit,'qty':qty,'cum':0.0,'placed':int(t),'status':'NEW','cancelRequested':False};self.placeHist.append((int(t),side,qty,limit));self.submits+=1;self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1
  credit,spend=self._pair_edge(side,limit,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
  L.update({'route':'ACTIVE','activeKey':key,'activeSubmittedAt':int(t)});self.q_active_remainder_cancel=False;self.q_counter['managedActiveSubmits']+=1
  self.q_events.append({'event':'QTY_MANAGED_ACTIVE_SUBMIT','t':int(t),'responsibilityId':rid,'sourceKey':pnd['sourceKey'],'key':key,'side':side,'role':role,'decisionAsk':ask,'limitPrice':limit,'qty':qty,'targetRemaining':target,'minimumActiveQty':min_qty,'submitRc':rc});return True
 def _manage_active_remainder(self,t):
  if self._inside_parent_process:return
  L=self.q_ladder
  if not L or L.get('route')!='ACTIVE' or self.q_active_remainder_cancel:return
  key=L.get('activeKey');o=self.orders.get(key)
  if not o:return
  try:s=self.snap(o)
  except Exception:return
  if base.v2.base.live(s.get('status')) and int(t)-int(L.get('activeSubmittedAt') or t)>=ACTIVE_WINDOW_MS:
   sid=next((slt for slt,k in self.slot_key.items() if k==key),None)
   if sid is not None and super()._request_cancel(t,int(sid),'QTY_MANAGED_ACTIVE_500MS_REMAINDER'):
    self.q_active_remainder_cancel=True;self.q_counter['activeRemainderCancels']+=1
 def _complete_carrier(self,t,reason):
  L=self.q_ladder
  if not L:return
  rid=L['responsibilityId'];remaining=self._lot_remaining(rid);self.q_counter['carrierServicesCompleted']+=1
  if remaining<=EPS:self.q_counter['responsibilitiesCompletedViaManagedService']+=1
  else:self.q_counter['partialServiceResponsibilityStillOpen']+=1
  self.q_events.append({'event':'QTY_CARRIER_SERVICE_COMPLETE','t':int(t),'responsibilityId':rid,'reason':reason,'carrierFillQty':float(L.get('carrierFillQty') or 0),'targetRemaining':remaining,'responsibilityCompleted':remaining<=EPS})
  self.q_ladder=None;self.q_pending_active=None;self.q_active_remainder_cancel=False
 def run_qty(self,winner='__UNSCORED__'):
  r=self.run_minimal(winner);r.update({'quantityResponsibilityLadderV3':True,'quantityLedgerSummary':self.ledger_summary(),'quantityLadderCounters':dict(self.q_counter),'quantityLadderEvents':self.q_events[:5000],'quantityResponsibilities':self.serializable_lots(),'quantityPaymentRows':self.resp_payment_rows,'quantityManagedRepairQtyDiagnostic':self.q_managed_repair_qty,'quantityManagedOverflowQtyDiagnostic':self.q_managed_overflow_qty,'openQuantityCarrierAtEnd':self.q_ladder,'pendingQuantityActiveAtEnd':self.q_pending_active});return r

def agg(rows,cell):
 xs=[r for r in rows if r['cell']==cell];pn=[float(r['pnlDiagnosticOnly']) for r in xs];n=len(xs)
 return {'markets':n,'tradeCoverage':sum(r['fillEvents']>0 for r in xs)/n,'twoSidedCoverage':sum(bool(r.get('twoSidedMaterialized')) for r in xs)/n,'wins':sum(x>EPS for x in pn),'losses':sum(x<-EPS for x in pn),'winRate':sum(x>EPS for x in pn)/n,'totalPnl':sum(pn),'avgPnl':sum(pn)/n,'worstPnl':min(pn),'bestPnl':max(pn),'avgFloor':sum(float(r['floor']) for r in xs)/n,'avgFills':sum(int(r['fillEvents']) for r in xs)/n,'avgAlternations':sum(int(r.get('fillSideAlternations') or 0) for r in xs)/n}
def pfields(r):
 ks=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','economicRepairQty','economicOverflowQty');return {k:r.get(k) for k in ks}
def eq(a,b):
 if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
 if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
 return a==b

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);ap.add_argument('--v1-reference');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 ref={}
 if a.v1_reference:
  d=json.loads(Path(a.v1_reference).read_text(encoding='utf-8'));ref={int(r['marketId']):r for r in d['rows'] if r.get('cell')=='B_RECURSIVE_REPAIR_LADDER_V1'}
 rows=[]
 with tempfile.TemporaryDirectory(prefix='qty_resp_v3_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
  for i,mid in enumerate(mids,1):
   tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
   A=v1.RecursiveRepairExecutionLadderSim(tape,True)
   try:ra=A.run_ladder('__UNSCORED__')
   finally:A.close()
   ra['pnlDiagnosticOnly']=float(ra['upQty' if winner=='UP' else 'downQty'])-float(ra['buyNotional']);ar={'marketId':mid,'cell':'A_V1_GENERATION_LADDER','winnerPostHocOnly':winner,**ra}
   if mid in ref:ar['referenceParity']={k:eq(pfields(ar)[k],pfields(ref[mid])[k]) for k in pfields(ar)}
   rows.append(ar)
   B=QuantityResponsibilityLadderV3(tape)
   try:rb=B.run_qty('__UNSCORED__')
   finally:B.close()
   rb['pnlDiagnosticOnly']=float(rb['upQty' if winner=='UP' else 'downQty'])-float(rb['buyNotional']);br={'marketId':mid,'cell':'B_V3_QUANTITY_LEDGER','winnerPostHocOnly':winner,**rb};rows.append(br)
   print(json.dumps({'progress':i,'marketId':mid,'A':{'pnl':ar['pnlDiagnosticOnly'],'floor':ar['floor'],'fills':ar['fillEvents'],'alts':ar['fillSideAlternations']},'B':{'pnl':br['pnlDiagnosticOnly'],'floor':br['floor'],'fills':br['fillEvents'],'alts':br['fillSideAlternations'],'ledger':br['quantityLedgerSummary'],'counters':br['quantityLadderCounters']}},ensure_ascii=False),flush=True)
 out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V3','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'summary':{'A_V1_GENERATION_LADDER':agg(rows,'A_V1_GENERATION_LADDER'),'B_V3_QUANTITY_LEDGER':agg(rows,'B_V3_QUANTITY_LEDGER')},'boundary':['exact FIFO quantity responsibility ledger is manager state','carrier != responsibility','partial carrier progress does not complete responsibility unless remainingQty=0','new same-clock residual cannot pay itself','dedicated enhanced ladder only when exact remaining debt >= venue minimum legal tranche','small debt remains in ledger for ordinary concurrent service','managed Active only after explicit Passive terminal zero-fill and current target still serviceable','Pair-only baseline remains concurrent','inside-spread/priority-loss/+1tick/500ms execution mechanisms inherited','no PnL/Floor/winner threshold action authority','<=180s inherited boundary','realistic HFT risk queue/250ms','no dream fill/no 8781']}
 v1.write_json(a.output,out);print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False))
if __name__=='__main__':main()
