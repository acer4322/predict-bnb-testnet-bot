"""Research-only integrated recursive Repair execution ladder V2 semantic-birth correction.

This candidate is a behavior integration of mechanisms already isolated on structural ETH5m cases:
- only PROBE_CORE/SATELLITE_EXPAND overflow creates queued Repair responsibility; Repair/Core payment overflow is Expand responsibility telemetry;
- at most one responsibility is managed by this ladder at a time; baseline max4 controller remains concurrent;
- the next inherited ECONOMIC_CORE/SATELLITE_REPAIR intent for that side may improve from its inherited Pair-legal level to ask-1 tick GTX;
- ordinary CORE_INVALIDATED / SATELLITE_FRONTIER_REANCHOR cancellation is suppressed for that managed carrier until inherited TTL;
- if best bid strictly moves above the carrier price while it remains zero-fill, cancel is requested immediately;
- a fill may still win the cancel/response race; Active is allowed only after explicit terminal zero-fill;
- same-intent Active uses source remaining qty with one side-price tick of latency protection and a 500ms remainder window;
- completion consumes exactly one queued responsibility; additional overflow births remain queued and can start later ladders.

No Target/winner/future information is runtime input. No fixed PnL/Floor/age/pair threshold is action authority.
Pair sum and Floor are diagnostics for the managed Repair route, not admission thresholds.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

import tools.run_eth_role_separated_minimal_pair_safety_smoke as base

EPS=1e-9
TICK=0.01
ACTIVE_WINDOW_MS=500
REPAIR_ROLES={"ECONOMIC_CORE","SATELLITE_REPAIR"}
REPAIR_BIRTH_SOURCE_ROLES={"PROBE_CORE","SATELLITE_EXPAND"}
TERMINAL={"FILLED","CANCELED","EXPIRED","REJECTED"}


def sha256(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()


def write_json(path,data):
 Path(path).parent.mkdir(parents=True,exist_ok=True)
 Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def eq(a,b):
 if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
 if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(eq(x,y) for x,y in zip(a,b))
 if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
 return a==b


class RecursiveRepairExecutionLadderSim(base.MinimalPairRoleSim):
 def __init__(self,tape,enabled:bool):
  super().__init__(tape,4,False)
  self.ladder_enabled=bool(enabled)
  self.fill_accounting=[]
  self._accounting_unkeyed=[]
  self.generation={"UP":0,"DOWN":0}
  self.handled_generation={"UP":0,"DOWN":0}
  self.generation_births=[]
  self.active_ladder=None
  self.arm=None
  self.pending_active=None
  self.active_remainder_cancel=False
  self.ladder_events=[]
  self.ladder_counters=Counter()
  self.managed_repair_qty=0.0
  self.managed_overflow_qty=0.0

 def record_fill(self,t,side,q,p):
  opp='DOWN' if side=='UP' else 'UP';left=float(q);matched=0.0;credit=0.0
  for lot_qty,lot_price in self.un[opp]:
   paid=min(left,float(lot_qty));matched+=paid;credit+=paid*(1.0-float(p)-float(lot_price));left-=paid
   if left<=EPS:break
  floor_before=self._physical_floor()
  super().record_fill(t,side,q,p)
  self._accounting_unkeyed.append({'t':int(t),'side':str(side),'confirmedQty':float(q),'executionPriceFromInheritedSubstrate':float(p),
    'matchedRepairQty':float(matched),'overflowQty':float(max(0.0,left)),'realizedFifoPairCredit':float(credit),
    'floorBefore':float(floor_before),'floorAfter':float(self._physical_floor()),'key':None,'role':'UNKNOWN'})

 def process(self,t):
  fill_before=len(self.fill_side_sequence);acc_before=len(self._accounting_unkeyed)
  super().process(t)
  fills=self.fill_side_sequence[fill_before:];acc=self._accounting_unkeyed[acc_before:]
  if len(fills)!=len(acc):raise RuntimeError('fill/accounting alignment mismatch')
  for f,a in zip(fills,acc):
   a['key']=f['key'];a['role']=f['role'];self.fill_accounting.append(a)
   if float(a['overflowQty'])>EPS and a['role'] in REPAIR_BIRTH_SOURCE_ROLES:
    repair_side='DOWN' if a['side']=='UP' else 'UP'
    self.generation[repair_side]+=1
    birth={'t':int(t),'generation':int(self.generation[repair_side]),'repairSide':repair_side,'sourceSide':a['side'],'sourceKey':a['key'],
           'sourceRole':a['role'],'overflowQty':float(a['overflowQty']),'executionPrice':float(a['executionPriceFromInheritedSubstrate']),
           'floorAfterSource':float(a['floorAfter'])}
    self.generation_births.append(birth);self.ladder_events.append({'event':'REPAIR_RESPONSIBILITY_BIRTH',**birth});self.ladder_counters['responsibilityBirths']+=1
   elif float(a['overflowQty'])>EPS and a['role'] in REPAIR_ROLES:
    # Target-grounded semantic correction: a Repair/Core carrier first pays old Repair debt;
    # any physical overflow is new favorable/Expand responsibility, not an immediate counter-Repair birth.
    self.ladder_counters['repairPaymentOverflowToExpandResponsibility']+=1
    self.ladder_events.append({'event':'REPAIR_PAYMENT_OVERFLOW_TO_EXPAND_RESPONSIBILITY','t':int(t),'sourceKey':a['key'],'sourceRole':a['role'],
      'side':a['side'],'overflowQty':float(a['overflowQty']),'executionPrice':float(a['executionPriceFromInheritedSubstrate']),
      'floorAfterSource':float(a['floorAfter'])})
   if self.active_ladder and f['key'] in {self.active_ladder.get('passiveKey'),self.active_ladder.get('activeKey')}:
    self.active_ladder['fillQty']=float(self.active_ladder.get('fillQty',0.0))+float(a['confirmedQty'])
    self.active_ladder['repairQty']=float(self.active_ladder.get('repairQty',0.0))+float(a['matchedRepairQty'])
    self.active_ladder['overflowQty']=float(self.active_ladder.get('overflowQty',0.0))+float(a['overflowQty'])
    self.managed_repair_qty+=float(a['matchedRepairQty']);self.managed_overflow_qty+=float(a['overflowQty'])
    self.ladder_events.append({'event':'MANAGED_FILL','t':int(t),'generation':self.active_ladder['generation'],'side':a['side'],'key':a['key'],
      'route':self.active_ladder.get('route'),'confirmedQty':a['confirmedQty'],'matchedRepairQty':a['matchedRepairQty'],'overflowQty':a['overflowQty'],
      'executionPrice':a['executionPriceFromInheritedSubstrate'],'floorBefore':a['floorBefore'],'floorAfter':a['floorAfter']})
  self._manage_active_remainder(t)

 def _pending_for_side(self,side):
  return int(self.generation[side])>int(self.handled_generation[side])

 def _arm_for_open(self,t,qv):
  if not self.ladder_enabled or self.active_ladder is not None or self.pending_active is not None:return None
  side,role,_,_=base.MinimalPairRoleSim._role_decision(self,qv)
  if role not in REPAIR_ROLES or not self._pending_for_side(side):return None
  return {'t':int(t),'side':side,'role':role,'generation':int(self.handled_generation[side])+1,'qv':qv}

 def _open_one_option(self,t,qv,end):
  if self.ladder_enabled and self.pending_active is not None:
   if int(end)-int(t)<=base.v2.NO_NEW_EXPOSURE_MS:
    self._complete_generation(t,'ACTIVE_BLOCKED_LATE_180S');self.pending_active=None
   elif self._submit_protected_active(t,qv):
    self.pending_active=None;return
  self.arm=self._arm_for_open(t,qv)
  try:return super()._open_one_option(t,qv,end)
  finally:self.arm=None

 def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
  original=super()._candidate_from_levels(side,require_pair,require_budget)
  a=self.arm
  if original is None or a is None or side!=a['side'] or a['role'] not in REPAIR_ROLES:return original
  p0,q0,proj=original;qv=a['qv'];bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);price=round(ask-TICK,10)
  used={round(float(x),10) for x in self._used_prices(side)}
  if not (EPS<bid<price<ask-EPS and price not in used and price>float(p0)+EPS):
   self.ladder_counters['noInsideSpreadImprovement']+=1;return original
  qty=1.0/price
  if qty>12.0+EPS:
   self.ladder_counters['managedQtyAbove12Fallback']+=1;return original
  opp='DOWN' if side=='UP' else 'UP';lots=[(float(q),float(p)) for q,p in self.un[opp]];gap=sum(q for q,_ in lots)
  if gap<=EPS:
   self.ladder_counters['noOppositeDebtFallback']+=1;return original
  avg=sum(q*p for q,p in lots)/gap
  a.update({'inheritedPrice':float(p0),'inheritedQty':float(q0),'passivePrice':float(price),'passiveQty':float(qty),'bidAtDecision':bid,'askAtDecision':ask,
            'oppositeUnmatchedQty':float(gap),'oppositeUnmatchedAverage':float(avg),'passivePairSum':float(avg+price),'passivePairOverage':float(avg+price-1.0)})
  return float(price),float(qty),proj

 def _submit_role(self,t,side,role,p,q,proj,source):
  before_n=self.n;ok=super()._submit_role(t,side,role,p,q,proj,source)
  a=self.arm
  if ok and self.ladder_enabled and self.active_ladder is None and a is not None and 'passivePrice' in a and side==a['side'] and role==a['role'] and abs(float(p)-float(a['passivePrice']))<=EPS:
   key=f'{side}_{before_n}'
   self.active_ladder={'generation':int(a['generation']),'side':side,'role':role,'route':'PASSIVE','passiveKey':key,'passiveSubmittedAt':int(t),
      'passivePrice':float(p),'passiveQty':float(q),'inheritedPrice':a['inheritedPrice'],'inheritedQty':a['inheritedQty'],
      'passivePairSum':a['passivePairSum'],'bidAtDecision':a['bidAtDecision'],'askAtDecision':a['askAtDecision'],
      'fillQty':0.0,'repairQty':0.0,'overflowQty':0.0,'priorityLossAt':None,'cancelSuppressed':0,'activeKey':None,'activeMeta':None}
   self.ladder_counters['managedPassiveSubmits']+=1
   self.ladder_events.append({'event':'MANAGED_PASSIVE_SUBMIT','t':int(t),**{k:self.active_ladder[k] for k in ('generation','side','role','passiveKey','passivePrice','passiveQty','passivePairSum','inheritedPrice')}})
  return ok

 def _request_cancel(self,t,sid,reason):
  key=self.slot_key.get(int(sid));L=self.active_ladder
  if self.ladder_enabled and L and L.get('route')=='PASSIVE' and key==L.get('passiveKey') and reason in {'CORE_INVALIDATED','SATELLITE_FRONTIER_REANCHOR'}:
   o=self.orders.get(key)
   if o is not None and int(t)-int(o['placed'])<int(base.v2.base.TTL) and L.get('priorityLossAt') is None:
    L['cancelSuppressed']=int(L.get('cancelSuppressed') or 0)+1;self.ladder_counters['ordinaryCancelSuppressed']+=1;return False
  return super()._request_cancel(t,sid,reason)

 def _reanchor_stale(self,t):
  L=self.active_ladder
  if self.ladder_enabled and L and L.get('route')=='PASSIVE' and L.get('priorityLossAt') is None:
   key=L.get('passiveKey');o=self.orders.get(key)
   if o is not None and not o.get('cancelRequested') and float(o.get('cum') or 0.0)<=EPS:
    qv=base.v2.base.quotes(self.book)
    if qv:
     bid=float(qv[L['side']]['bid']);ask=float(qv[L['side']]['ask']);own=float(o['price'])
     if bid>own+EPS:
      sid=next((s for s,k in self.slot_key.items() if k==key),None)
      if sid is not None:
       ok=super()._request_cancel(t,int(sid),'MANAGED_PRIORITY_LOSS')
       if ok:
        L['priorityLossAt']=int(t);L['priorityLossBid']=bid;L['priorityLossAsk']=ask;self.ladder_counters['priorityLossCancels']+=1
        self.ladder_events.append({'event':'MANAGED_PRIORITY_LOSS_CANCEL','t':int(t),'generation':L['generation'],'key':key,'side':L['side'],'ownPrice':own,'bestBid':bid,'bestAsk':ask,'ageMs':int(t)-int(o['placed'])})
  return super()._reanchor_stale(t)

 def _refresh_slots(self,t):
  start=len(self.slot_history);super()._refresh_slots(t)
  releases=[x for x in self.slot_history[start:] if x.get('event')=='SLOT_RELEASE']
  L=self.active_ladder
  if not L:return
  for e in releases:
   key=e.get('key');status=str(e.get('status') or '').upper();cum=float(e.get('cum') or 0.0)
   if L.get('route')=='PASSIVE' and key==L.get('passiveKey'):
    self.ladder_events.append({'event':'MANAGED_PASSIVE_TERMINAL','t':int(t),'generation':L['generation'],'key':key,'status':status,'cum':cum,'priorityLossAt':L.get('priorityLossAt')})
    if cum>EPS or status=='FILLED':
     self.ladder_counters['managedPassivePhysicalSuccess']+=1;self._complete_generation(t,'PASSIVE_PROGRESS')
    else:
     o=self.orders.get(key);remaining=max(0.0,float(o.get('qty') or L.get('passiveQty') or 0)-float(o.get('cum') or 0)) if o else float(L.get('passiveQty') or 0)
     self.pending_active={'generation':L['generation'],'side':L['side'],'role':L['role'],'sourceKey':key,'remainingQty':remaining,'sourceStatus':status,'sourceTerminalAt':int(t),
                          'sourcePairSum':L.get('passivePairSum'),'priorityLossAt':L.get('priorityLossAt')}
     self.ladder_counters['managedPassiveZeroFillTerminal']+=1
     # keep generation ownership while route changes
     L['route']='PENDING_ACTIVE'
   elif L.get('route')=='ACTIVE' and key==L.get('activeKey'):
    self.ladder_events.append({'event':'MANAGED_ACTIVE_TERMINAL','t':int(t),'generation':L['generation'],'key':key,'status':status,'cum':cum})
    if cum>EPS:self.ladder_counters['managedActivePhysicalSuccess']+=1
    else:self.ladder_counters['managedActiveZeroFillTerminal']+=1
    self._complete_generation(t,'ACTIVE_TERMINAL')
  self._manage_active_remainder(t)

 def _submit_protected_active(self,t,qv):
  pnd=self.pending_active;L=self.active_ladder
  if pnd is None or L is None:return False
  side=pnd['side'];role=pnd['role'];qty=float(pnd['remainingQty']);bid=float(qv[side]['bid']);ask=float(qv[side]['ask'])
  if qty<=EPS:self._complete_generation(t,'ZERO_ACTIVE_REMAINDER');return False
  if len(self.slot_key)>=self.max_slots:
   self.ladder_counters['activeNoFreeSlot']+=1;return False
  free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
  if free is None:self.ladder_counters['activeNoFreeSlot']+=1;return False
  limit=round(min(0.99,ask+TICK),10);prot=1 if limit>ask+EPS else 0
  n=self.n;self.n+=1;ex=base.v2.base.ex;native_side,native_price=ex.native_order(side,limit)
  try:
   if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
   else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
  except Exception:
   self.ladder_counters['activeSubmitException']+=1;return False
  key=f'{side}_{n}';self.orders[key]={'n':n,'side':side,'price':limit,'qty':qty,'cum':0.0,'placed':int(t),'status':'NEW','cancelRequested':False}
  self.placeHist.append((int(t),side,qty,limit));self.submits+=1;self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1
  credit,spend=self._pair_edge(side,limit,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
  opp='DOWN' if side=='UP' else 'UP';oq=sum(float(a) for a,_ in self.un[opp]);avg=self.unmatched_avg(opp) if oq>EPS else None
  L.update({'route':'ACTIVE','activeKey':key,'activeSubmittedAt':int(t),'activeMeta':{'decisionAsk':ask,'limitPrice':limit,'protectionTicks':prot,'qty':qty,
     'pairSum':(float(avg)+limit) if avg is not None else None,'bidAtRelay':bid,'askAtRelay':ask,'submitRc':rc}})
  self.active_remainder_cancel=False;self.ladder_counters['managedActiveSubmits']+=1
  self.ladder_events.append({'event':'MANAGED_ACTIVE_SUBMIT','t':int(t),'generation':L['generation'],'sourceKey':pnd['sourceKey'],'key':key,'side':side,'role':role,
     'decisionAsk':ask,'limitPrice':limit,'protectionTicks':prot,'qty':qty,'pairSum':L['activeMeta']['pairSum']})
  return True

 def _manage_active_remainder(self,t):
  L=self.active_ladder
  if not self.ladder_enabled or not L or L.get('route')!='ACTIVE' or self.active_remainder_cancel:return
  key=L.get('activeKey');o=self.orders.get(key)
  if not o:return
  try:s=self.snap(o)
  except Exception:return
  if base.v2.base.live(s.get('status')) and int(t)-int(L.get('activeSubmittedAt') or t)>=ACTIVE_WINDOW_MS:
   sid=next((slt for slt,k in self.slot_key.items() if k==key),None)
   if sid is not None and super()._request_cancel(t,int(sid),'MANAGED_ACTIVE_500MS_REMAINDER'):
    self.active_remainder_cancel=True;self.ladder_counters['activeRemainderCancels']+=1

 def _complete_generation(self,t,reason):
  L=self.active_ladder
  if not L:return
  side=L['side'];gen=int(L['generation']);self.handled_generation[side]=max(int(self.handled_generation[side]),gen)
  self.ladder_counters['generationsHandled']+=1
  self.ladder_events.append({'event':'MANAGED_GENERATION_COMPLETE','t':int(t),'generation':gen,'side':side,'reason':reason,
      'fillQty':float(L.get('fillQty') or 0),'repairQty':float(L.get('repairQty') or 0),'overflowQty':float(L.get('overflowQty') or 0),
      'queuedRemaining':max(0,int(self.generation[side])-int(self.handled_generation[side]))})
  self.active_ladder=None;self.pending_active=None;self.active_remainder_cancel=False

 def run_ladder(self,winner='__UNSCORED__'):
  r=self.run_minimal(winner)
  r.update({'recursiveRepairLadderVersion':'V2_SEMANTIC_BIRTH','repairBirthSourceRoles':sorted(REPAIR_BIRTH_SOURCE_ROLES),'recursiveRepairLadderEnabled':self.ladder_enabled,'responsibilityGeneration':dict(self.generation),'responsibilityHandled':dict(self.handled_generation),
            'responsibilityBirths':self.generation_births,'ladderEvents':self.ladder_events[:4000],'ladderCounters':dict(self.ladder_counters),
            'confirmedFillAccounting':self.fill_accounting,'economicRepairQty':float(sum(x['matchedRepairQty'] for x in self.fill_accounting)),
            'economicOverflowQty':float(sum(x['overflowQty'] for x in self.fill_accounting)),'managedRepairQty':float(self.managed_repair_qty),
            'managedOverflowQty':float(self.managed_overflow_qty),'openManagedLadderAtEnd':self.active_ladder,'pendingActiveAtEnd':self.pending_active,
            'runtimeWinnerInputUsed':False})
  return r


def summarize(r):
 c=r.get('ladderCounters') or {}
 return {'submits':int(r['submits']),'fills':int(r['fillEvents']),'filledQty':float(r['filledQty']),'alts':int(r.get('fillSideAlternations') or 0),
   'twoSided':bool(r.get('twoSidedMaterialized')),'repairQty':float(r.get('economicRepairQty') or 0),'overflowQty':float(r.get('economicOverflowQty') or 0),
   'births':int(c.get('responsibilityBirths',0)),'handled':int(c.get('generationsHandled',0)),'managedPassive':int(c.get('managedPassiveSubmits',0)),
   'managedPassiveSuccess':int(c.get('managedPassivePhysicalSuccess',0)),'active':int(c.get('managedActiveSubmits',0)),'activeSuccess':int(c.get('managedActivePhysicalSuccess',0)),
   'managedRepairQty':float(r.get('managedRepairQty') or 0),'pnl':float(r.get('pnlDiagnosticOnly') or 0),'floor':float(r.get('floor') or 0),'best':float(r.get('best') or 0)}


def agg(rows,cell):
 xs=[r for r in rows if r['cell']==cell];pn=[float(r['pnlDiagnosticOnly']) for r in xs];n=len(xs)
 return {'markets':n,'tradeCoverage':sum(int(r['fillEvents'])>0 for r in xs)/n,'twoSidedCoverage':sum(bool(r.get('twoSidedMaterialized')) for r in xs)/n,
   'wins':sum(x>EPS for x in pn),'losses':sum(x<-EPS for x in pn),'flats':sum(abs(x)<=EPS for x in pn),'winRate':sum(x>EPS for x in pn)/n,
   'totalPnl':sum(pn),'avgPnl':sum(pn)/n,'worstPnl':min(pn),'bestPnl':max(pn),'avgFloor':sum(float(r['floor']) for r in xs)/n,'worstFloor':min(float(r['floor']) for r in xs),
   'avgFills':sum(int(r['fillEvents']) for r in xs)/n,'avgAlternations':sum(int(r.get('fillSideAlternations') or 0) for r in xs)/n,
   'totalRepairQty':sum(float(r.get('economicRepairQty') or 0) for r in xs),'managedRepairQty':sum(float(r.get('managedRepairQty') or 0) for r in xs),
   'managedPassiveSubmits':sum(int((r.get('ladderCounters') or {}).get('managedPassiveSubmits',0)) for r in xs),
   'managedActiveSubmits':sum(int((r.get('ladderCounters') or {}).get('managedActiveSubmits',0)) for r in xs),
   'managedGenerationsHandled':sum(int((r.get('ladderCounters') or {}).get('generationsHandled',0)) for r in xs)}


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);ap.add_argument('--include-baseline',action='store_true');a=ap.parse_args()
 mids=[int(x) for x in a.market_ids.split(',') if x.strip()];op=Path(a.output).resolve()
 if op.exists():ap.error('do not overwrite')
 tmp=Path(tempfile.mkdtemp(prefix='recursive_repair_ladder_v1_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for mid in mids:
    if mid not in co:raise RuntimeError(f'missing market {mid}')
    z.extract(f'tapes/{mid}.json.xz',tmp)
  rows=[]
  for i,mid in enumerate(mids,1):
   tape=tmp/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
   cells=[('A_PAIR_ONLY_BASELINE',False),('B_RECURSIVE_REPAIR_LADDER_V1',True)] if a.include_baseline else [('B_RECURSIVE_REPAIR_LADDER_V1',True)]
   for cell,en in cells:
    sim=RecursiveRepairExecutionLadderSim(tape,en)
    try:r=sim.run_ladder('__UNSCORED__')
    finally:sim.close()
    r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional'])
    row={'marketId':mid,'winnerPostHocOnly':winner,'cell':cell,'tapeSha256':sha256(tape),**r};rows.append(row)
    print(json.dumps({'progress':i,'of':len(mids),'marketId':mid,'cell':cell,**summarize(row)},ensure_ascii=False),flush=True)
  summary={cell:agg(rows,cell) for cell in sorted({r['cell'] for r in rows})}
  out={'version':'ETH_RECURSIVE_REPAIR_EXECUTION_LADDER_V2_SEMANTIC_BIRTH','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'bundleSha256':sha256(a.bundle),
       'summary':summary,'rows':rows,'graduationTarget':{'markets':24,'winRateGte':0.50,'avgPnlGt':2.0},
       'boundary':['RoleSeparated Pair-only baseline remains concurrent','one managed ladder globally, one tranche per overflow-birth generation','inside-spread price improvement only when inherited Pair-legal candidate already exists','ordinary managed-carrier reanchor suppressed until TTL; priority-loss cancel-race may act earlier','Active only after explicit terminal zero-fill','Active same side/role/source remaining qty; limit=current ask+1 tick capped at 0.99; 500ms remainder','no PnL/Floor/age/pair threshold action authority','<=180s inherited market-time boundary','realistic HFT risk queue, 250ms entry/response, no dream fill','winner post-hoc only; no Target runtime; no 8781']}
  write_json(op,out);print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
