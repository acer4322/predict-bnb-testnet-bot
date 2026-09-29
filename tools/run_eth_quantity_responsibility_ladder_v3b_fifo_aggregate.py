"""V3B: exact FIFO lots remain accounting truth, but one execution carrier owns a serviceable FIFO queue side.

Corrects V3's single-oldest-lot feasibility error. A carrier can pay the oldest responsibility and
continue into later same-side FIFO responsibilities within the same confirmed fill. Serviceability
therefore uses aggregate outstanding on that Expand-responsibility side. No economic threshold changed.
"""
from __future__ import annotations
import argparse,collections,importlib.util,json,math,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_quantity_responsibility_ladder_v3.py'
if _STAGED.exists():
 spec=importlib.util.spec_from_file_location('staged_qty_v3',_STAGED);v3=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3)
else:
 import tools.run_eth_quantity_responsibility_ladder_v3 as v3
qshadow=v3.qshadow;v1=v3.v1;base=v3.base;EPS=v3.EPS;TICK=v3.TICK;ACTIVE_WINDOW_MS=v3.ACTIVE_WINDOW_MS;REPAIR_ROLES=v3.REPAIR_ROLES

class FifoAggregateResponsibilityLadderV3B(v3.QuantityResponsibilityLadderV3):
 def _aggregate_outstanding_expand_side(self,expand_side):
  return float(sum(float(x['remainingQty']) for x in self.resp_queues[str(expand_side)]))
 def _aggregate_for_repair_side(self,repair_side):
  return self._aggregate_outstanding_expand_side(qshadow.opp(repair_side))
 def process(self,t):
  # Bypass V3 single-lot ownership logic while retaining the already-validated runtime atomic FIFO ledger.
  before=len(self.fill_accounting);self._inside_parent_process=True
  try:qshadow.QuantityLedgerShadowSim.process(self,t)
  finally:self._inside_parent_process=False
  new=self.fill_accounting[before:];L=self.q_ladder
  if L:
   target=self._aggregate_outstanding_expand_side(L['targetExpandSide'])
   for a in new:
    if a['key'] in {L.get('passiveKey'),L.get('activeKey')}:
     L['carrierFillQty']=float(L.get('carrierFillQty',0.0))+float(a['confirmedQty']);L['carrierRepairQty']=float(L.get('carrierRepairQty',0.0))+float(a['matchedRepairQty']);L['carrierOverflowQty']=float(L.get('carrierOverflowQty',0.0))+float(a['overflowQty'])
     self.q_managed_repair_qty+=float(a['matchedRepairQty']);self.q_managed_overflow_qty+=float(a['overflowQty'])
     self.q_events.append({'event':'QTY_FIFO_MANAGED_FILL','t':int(t),'originResponsibilityId':L['originResponsibilityId'],'targetExpandSide':L['targetExpandSide'],'route':L.get('route'),'key':a['key'],'confirmedQty':a['confirmedQty'],'targetAggregateRemainingAfterClock':target,'executionPrice':a['executionPriceFromInheritedSubstrate']})
   if target<=EPS and L.get('route')=='PASSIVE':
    key=L.get('passiveKey');o=self.orders.get(key)
    if o is not None and float(o.get('cum') or 0.0)<=EPS and not o.get('cancelRequested'):
     sid=next((s for s,k in self.slot_key.items() if k==key),None)
     if sid is not None and super(v3.QuantityResponsibilityLadderV3,self)._request_cancel(t,int(sid),'QTY_FIFO_QUEUE_SATISFIED_ELSEWHERE'):
      self.q_counter['queueSatisfiedElsewhereCancels']+=1;L['satisfiedElsewhere']=True
      self.q_events.append({'event':'QTY_FIFO_QUEUE_SATISFIED_ELSEWHERE_CANCEL','t':int(t),'originResponsibilityId':L['originResponsibilityId'],'targetExpandSide':L['targetExpandSide'],'key':key})
  self._manage_active_remainder(t)
 def _arm_for_open_qty(self,t,qv):
  if not self.qty_enabled or self.q_ladder is not None or self.q_pending_active is not None:return None
  side,role,_,_=base.MinimalPairRoleSim._role_decision(self,qv)
  if role not in REPAIR_ROLES:return None
  lot=self._oldest_for_repair_side(side)
  if lot is None:return None
  agg=self._aggregate_for_repair_side(side)
  if agg<=EPS:return None
  return {'t':int(t),'side':side,'role':role,'responsibilityId':int(lot['id']),'oldestRemainingQty':float(lot['remainingQty']),'aggregateOutstandingQty':agg,'bornAt':int(lot['bornAt']),'expandSide':lot['side'],'expandPrice':float(lot['price']),'qv':qv}
 def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
  # Direct Pair-only candidate; do not call V3 single-lot feasibility override.
  original=base.MinimalPairRoleSim._candidate_from_levels(self,side,require_pair,require_budget);a=self.q_arm
  if original is None or a is None or side!=a['side']:return original
  p0,q0,proj=original;qv=a['qv'];bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);price=round(ask-TICK,10);used={round(float(x),10) for x in self._used_prices(side)}
  if not (EPS<bid<price<ask-EPS and price not in used and price>float(p0)+EPS):self.q_counter['noInsideSpreadImprovement']+=1;return original
  qty=1.0/price;aggregate=self._aggregate_for_repair_side(side);oldest=float(self._oldest_for_repair_side(side)['remainingQty']) if self._oldest_for_repair_side(side) else 0.0
  if aggregate+EPS<qty:
   self.q_counter['aggregateDebtBelowDedicatedMinimum']+=1;self.q_events.append({'event':'QTY_FIFO_AGGREGATE_DEFERRED_BELOW_MINIMUM','t':int(a['t']),'originResponsibilityId':a['responsibilityId'],'oldestRemainingQty':oldest,'aggregateRemainingQty':aggregate,'minimumCarrierQty':qty,'side':side});return original
  if oldest+EPS<qty:self.q_counter['crossLotCarrierAdmissions']+=1
  if qty>12.0+EPS:self.q_counter['managedQtyAbove12Fallback']+=1;return original
  a.update({'inheritedPrice':float(p0),'inheritedQty':float(q0),'passivePrice':float(price),'passiveQty':float(qty),'bidAtDecision':bid,'askAtDecision':ask,'oldestRemainingAtSubmit':oldest,'aggregateRemainingAtSubmit':aggregate,'passivePairSumOldest':float(a['expandPrice']+price)})
  return float(price),float(qty),proj
 def _submit_role(self,t,side,role,p,q,proj,source):
  # Bypass V3 marking; Pair-only physical submit is unchanged.
  before_n=self.n;ok=base.MinimalPairRoleSim._submit_role(self,t,side,role,p,q,proj,source);a=self.q_arm
  if ok and self.q_ladder is None and a is not None and 'passivePrice' in a and side==a['side'] and role==a['role'] and abs(float(p)-float(a['passivePrice']))<=EPS:
   key=f'{side}_{before_n}';self.q_ladder={'originResponsibilityId':a['responsibilityId'],'targetExpandSide':a['expandSide'],'side':side,'role':role,'route':'PASSIVE','passiveKey':key,'passiveSubmittedAt':int(t),'passivePrice':float(p),'passiveQty':float(q),'inheritedPrice':a['inheritedPrice'],'targetOutstandingAtSubmit':a['aggregateRemainingAtSubmit'],'oldestRemainingAtSubmit':a['oldestRemainingAtSubmit'],'carrierFillQty':0.0,'carrierRepairQty':0.0,'carrierOverflowQty':0.0,'priorityLossAt':None,'activeKey':None,'satisfiedElsewhere':False}
   self.q_counter['managedPassiveSubmits']+=1;self.q_events.append({'event':'QTY_FIFO_MANAGED_PASSIVE_SUBMIT','t':int(t),'originResponsibilityId':a['responsibilityId'],'targetExpandSide':a['expandSide'],'side':side,'role':role,'key':key,'price':float(p),'qty':float(q),'oldestRemaining':a['oldestRemainingAtSubmit'],'aggregateRemaining':a['aggregateRemainingAtSubmit']})
  return ok
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
      if sid is not None and super(v3.QuantityResponsibilityLadderV3,self)._request_cancel(t,int(sid),'QTY_FIFO_MANAGED_PRIORITY_LOSS'):
       L['priorityLossAt']=int(t);self.q_counter['priorityLossCancels']+=1;self.q_events.append({'event':'QTY_FIFO_MANAGED_PRIORITY_LOSS_CANCEL','t':int(t),'originResponsibilityId':L['originResponsibilityId'],'targetExpandSide':L['targetExpandSide'],'key':key,'ownPrice':own,'bestBid':bid,'bestAsk':ask})
  # RoleSeparated Pair-only stale logic will call our inherited _request_cancel, preserving managed retention.
  return base.MinimalPairRoleSim._reanchor_stale(self,t)
 def _refresh_slots(self,t):
  start=len(self.slot_history);qshadow.v1.RecursiveRepairExecutionLadderSim._refresh_slots(self,t);releases=[x for x in self.slot_history[start:] if x.get('event')=='SLOT_RELEASE'];L=self.q_ladder
  if not L:return
  for e in releases:
   key=e.get('key');status=str(e.get('status') or '').upper();cum=float(e.get('cum') or 0.0);target=self._aggregate_outstanding_expand_side(L['targetExpandSide'])
   if L.get('route')=='PASSIVE' and key==L.get('passiveKey'):
    self.q_events.append({'event':'QTY_FIFO_MANAGED_PASSIVE_TERMINAL','t':int(t),'originResponsibilityId':L['originResponsibilityId'],'targetExpandSide':L['targetExpandSide'],'key':key,'status':status,'cum':cum,'targetAggregateRemaining':target})
    if cum>EPS or status=='FILLED':self.q_counter['managedPassivePhysicalSuccess']+=1;self._complete_carrier(t,'PASSIVE_PROGRESS')
    elif target<=EPS or L.get('satisfiedElsewhere'):self.q_counter['managedPassiveTargetSatisfiedWithoutFill']+=1;self._complete_carrier(t,'QUEUE_SATISFIED_ELSEWHERE')
    else:
     o=self.orders.get(key);remaining=max(0.0,float(o.get('qty') or L.get('passiveQty') or 0)-float(o.get('cum') or 0)) if o else float(L.get('passiveQty') or 0)
     self.q_pending_active={'originResponsibilityId':L['originResponsibilityId'],'targetExpandSide':L['targetExpandSide'],'side':L['side'],'role':L['role'],'sourceKey':key,'sourceRemainingQty':remaining,'sourceTerminalAt':int(t)};L['route']='PENDING_ACTIVE';self.q_counter['managedPassiveZeroFillTerminal']+=1
   elif L.get('route')=='ACTIVE' and key==L.get('activeKey'):
    self.q_events.append({'event':'QTY_FIFO_MANAGED_ACTIVE_TERMINAL','t':int(t),'originResponsibilityId':L['originResponsibilityId'],'targetExpandSide':L['targetExpandSide'],'key':key,'status':status,'cum':cum,'targetAggregateRemaining':target})
    if cum>EPS:self.q_counter['managedActivePhysicalSuccess']+=1
    else:self.q_counter['managedActiveZeroFillTerminal']+=1
    self._complete_carrier(t,'ACTIVE_TERMINAL')
  self._manage_active_remainder(t)
 def _submit_protected_active_qty(self,t,qv):
  pnd=self.q_pending_active;L=self.q_ladder
  if pnd is None or L is None:return False
  target=self._aggregate_outstanding_expand_side(pnd['targetExpandSide'])
  if target<=EPS:self.q_counter['activeSuppressedQueueSatisfied']+=1;self._complete_carrier(t,'QUEUE_SATISFIED_BEFORE_ACTIVE');return False
  side=pnd['side'];role=pnd['role'];ask=float(qv[side]['ask']);bid=float(qv[side]['bid']);limit=round(min(.99,ask+TICK),10);min_qty=1.0/limit;source_remaining=float(pnd['sourceRemainingQty']);qty=min(source_remaining,target)
  if qty+EPS<min_qty:
   self.q_counter['activeDeferredAggregateBelowMinimum']+=1;self.q_events.append({'event':'QTY_FIFO_ACTIVE_DEFERRED_BELOW_MINIMUM','t':int(t),'originResponsibilityId':pnd['originResponsibilityId'],'targetExpandSide':pnd['targetExpandSide'],'aggregateRemaining':target,'sourceRemainingQty':source_remaining,'minimumActiveQty':min_qty});self._complete_carrier(t,'ACTIVE_DEFERRED_AGGREGATE_BELOW_MINIMUM');return False
  if len(self.slot_key)>=self.max_slots:self.q_counter['activeNoFreeSlot']+=1;return False
  free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
  if free is None:self.q_counter['activeNoFreeSlot']+=1;return False
  n=self.n;self.n+=1;ex=base.v2.base.ex;native_side,native_price=ex.native_order(side,limit)
  try:
   if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
   else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
  except Exception:self.q_counter['activeSubmitException']+=1;return False
  key=f'{side}_{n}';self.orders[key]={'n':n,'side':side,'price':limit,'qty':qty,'cum':0.0,'placed':int(t),'status':'NEW','cancelRequested':False};self.placeHist.append((int(t),side,qty,limit));self.submits+=1;self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1;credit,spend=self._pair_edge(side,limit,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
  L.update({'route':'ACTIVE','activeKey':key,'activeSubmittedAt':int(t)});self.q_active_remainder_cancel=False;self.q_counter['managedActiveSubmits']+=1;self.q_events.append({'event':'QTY_FIFO_MANAGED_ACTIVE_SUBMIT','t':int(t),'originResponsibilityId':pnd['originResponsibilityId'],'targetExpandSide':pnd['targetExpandSide'],'sourceKey':pnd['sourceKey'],'key':key,'side':side,'role':role,'decisionAsk':ask,'limitPrice':limit,'qty':qty,'targetAggregateRemaining':target,'minimumActiveQty':min_qty,'submitRc':rc});return True
 def _complete_carrier(self,t,reason):
  L=self.q_ladder
  if not L:return
  remaining=self._aggregate_outstanding_expand_side(L['targetExpandSide']);self.q_counter['carrierServicesCompleted']+=1
  if remaining<=EPS:self.q_counter['queuesCompletedViaManagedService']+=1
  else:self.q_counter['partialServiceQueueStillOpen']+=1
  self.q_events.append({'event':'QTY_FIFO_CARRIER_SERVICE_COMPLETE','t':int(t),'originResponsibilityId':L['originResponsibilityId'],'targetExpandSide':L['targetExpandSide'],'reason':reason,'carrierFillQty':float(L.get('carrierFillQty') or 0),'targetAggregateRemaining':remaining,'queueCompleted':remaining<=EPS});self.q_ladder=None;self.q_pending_active=None;self.q_active_remainder_cancel=False
 def run_qty(self,winner='__UNSCORED__'):
  r=super().run_qty(winner);r['quantityResponsibilityLadderV3B']='FIFO_AGGREGATE_SERVICE';r['quantityLadderVersion']='V3B_FIFO_AGGREGATE';return r

def agg(rows,cell):return v3.agg(rows,cell)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
 with tempfile.TemporaryDirectory(prefix='qty_v3b_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
  for i,mid in enumerate(mids,1):
   tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
   for cell,Cls in [('A_V3_SINGLE_LOT',v3.QuantityResponsibilityLadderV3),('B_V3B_FIFO_AGGREGATE',FifoAggregateResponsibilityLadderV3B)]:
    sim=Cls(tape)
    try:r=sim.run_qty('__UNSCORED__')
    finally:sim.close()
    r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row)
    print(json.dumps({'progress':i,'marketId':mid,'cell':cell,'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'ledger':row['quantityLedgerSummary'],'counters':row['quantityLadderCounters']},ensure_ascii=False),flush=True)
 out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V3B_FIFO_AGGREGATE','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'summary':{'A_V3_SINGLE_LOT':agg(rows,'A_V3_SINGLE_LOT'),'B_V3B_FIFO_AGGREGATE':agg(rows,'B_V3B_FIFO_AGGREGATE')},'boundary':['exact FIFO lots unchanged','carrier service ownership is FIFO queue side/aggregate outstanding','carrier may pay across multiple FIFO lots','aggregate outstanding >= venue minimum is structural dedicated-service feasibility','small aggregate remains ledgered for ordinary service','no PnL/Floor/winner threshold','execution ladder otherwise unchanged','no NEW24-B/no 8781/no dream fill']};v1.write_json(a.output,out);print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False))
if __name__=='__main__':main()
