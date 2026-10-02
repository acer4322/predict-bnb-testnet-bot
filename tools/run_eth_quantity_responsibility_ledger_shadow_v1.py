"""Behavior-inert runtime exact-quantity responsibility ledger shadow.

Runs the frozen Recursive Repair Ladder V1 behavior unchanged while reconstructing a
separate atomic FIFO responsibility ledger at each confirmed-fill receipt clock.
The ledger has no action authority. It exists to prove that exact remaining-quantity
semantics can be maintained online before a later candidate is allowed to use it.
"""
from __future__ import annotations
import argparse,collections,json,math,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import importlib.util
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_recursive_repair_execution_ladder_v1.py'
if _STAGED.exists():
 spec=importlib.util.spec_from_file_location('staged_recursive_v1',_STAGED);v1=importlib.util.module_from_spec(spec);spec.loader.exec_module(v1)
else:
 import tools.run_eth_recursive_repair_execution_ladder_v1 as v1
EPS=1e-9; SIDES=('UP','DOWN')
def opp(s):return 'DOWN' if s=='UP' else 'UP'

class QuantityLedgerShadowSim(v1.RecursiveRepairExecutionLadderSim):
 def __init__(self,tape,enabled=True):
  super().__init__(tape,enabled)
  self.resp_queues={s:collections.deque() for s in SIDES};self.resp_all=[];self.resp_next_id=1
  self.resp_clock=collections.Counter();self.resp_viol=collections.Counter();self.resp_payment_rows=[]
  self._ledger_accounted_n=0
 def process(self,t):
  before=len(self.fill_accounting)
  super().process(t)
  legs=self.fill_accounting[before:]
  if legs:self._ledger_batch(int(t),legs)
 def _ledger_batch(self,t,legs):
  self.resp_clock['clocks']+=1
  agg={s:{'q':0.0,'notional':0.0,'roles':collections.Counter()} for s in SIDES}
  for x in legs:
   s=str(x['side']).upper();q=float(x['confirmedQty']);p=float(x['executionPriceFromInheritedSubstrate']);role=str(x.get('role') or 'UNKNOWN')
   if s not in SIDES or q<=EPS:continue
   agg[s]['q']+=q;agg[s]['notional']+=q*p;agg[s]['roles'][role]+=q
  for s in SIDES:agg[s]['px']=agg[s]['notional']/agg[s]['q'] if agg[s]['q']>EPS else None
  inp={s:agg[s]['q'] for s in SIDES};rem=dict(inp);repair={s:0.0 for s in SIDES};birth={s:0.0 for s in SIDES}
  # Existing opposite-side Expand responsibility is paid first.
  for pay in SIDES:
   debt_side=opp(pay);dq=self.resp_queues[debt_side];need=rem[pay]
   while need>EPS and dq:
    lot=dq[0];take=min(need,float(lot['remainingQty']))
    if take<=EPS:break
    before=float(lot['remainingQty']);lot['remainingQty']-=take;lot['paidQty']+=take;lot['paymentClocks'].add(t);lot['lastPaidAt']=t;repair[pay]+=take;need-=take
    self.resp_payment_rows.append({'t':t,'responsibilityId':lot['id'],'expandSide':debt_side,'repairSide':pay,'qty':take,'remainingBefore':before,'remainingAfter':lot['remainingQty']})
    if lot['remainingQty']<=EPS:
     done=dq.popleft();done['remainingQty']=0.0;done['completedAt']=t;done['durationMs']=t-int(done['bornAt']);done['paymentClockCount']=len(done['paymentClocks'])
   rem[pay]=need
  pair_now=min(rem['UP'],rem['DOWN'])
  if pair_now>EPS:
   rem['UP']-=pair_now;rem['DOWN']-=pair_now;self.resp_clock['directPairClocks']+=1
  # One-sided same-clock residual births new responsibility; cannot pay itself this clock.
  for s in SIDES:
   q=rem[s]
   if q<=EPS:continue
   rt=sum(agg[s]['roles'].values());mix={k:v/rt for k,v in agg[s]['roles'].items()} if rt>EPS else {}
   lot={'id':self.resp_next_id,'side':s,'repairSide':opp(s),'bornAt':t,'initialQty':float(q),'remainingQty':float(q),'paidQty':0.0,'price':float(agg[s]['px']),'sourceRoleMix':mix,'paymentClocks':set(),'completedAt':None}
   self.resp_next_id+=1;self.resp_queues[s].append(lot);self.resp_all.append(lot);birth[s]+=q
  if sum(repair.values())>EPS:self.resp_clock['repairClocks']+=1
  if sum(birth.values())>EPS:self.resp_clock['expandBirthClocks']+=1
  if sum(repair.values())>EPS and sum(birth.values())>EPS:self.resp_clock['repairThenNewExpandCompositeClocks']+=1
  out={s:sum(float(x['remainingQty']) for x in self.resp_queues[s]) for s in SIDES}
  physical_gap=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
  if abs(out['UP']+out['DOWN']-physical_gap)>1e-6:self.resp_viol['outstandingGapMismatch']+=1
  if out['UP']>EPS and out['DOWN']>EPS:self.resp_viol['twoOutstandingSides']+=1
  for s in SIDES:
   for lot in self.resp_queues[s]:
    if lot['remainingQty']<-1e-7 or lot['paidQty']>lot['initialQty']+1e-7:self.resp_viol['lotOverpaid']+=1
 def ledger_summary(self):
  done=[x for x in self.resp_all if x.get('completedAt') is not None];pays=[len(x['paymentClocks']) for x in done];dur=[x['durationMs'] for x in done]
  return {'responsibilitiesBorn':len(self.resp_all),'responsibilitiesCompleted':len(done),'multiPaymentCompleted':sum(x>=2 for x in pays),
   'multiPaymentCompletedShare':sum(x>=2 for x in pays)/len(done) if done else None,'paymentsMean':sum(pays)/len(pays) if pays else None,'paymentsMax':max(pays) if pays else None,
   'durationMedianMs':sorted(dur)[len(dur)//2] if dur else None,'terminalOutstandingQty':sum(float(x['remainingQty']) for x in self.resp_all),
   'clockTopology':dict(self.resp_clock),'invariantViolations':dict(self.resp_viol),'outstandingBySide':{s:sum(float(x['remainingQty']) for x in self.resp_queues[s]) for s in SIDES}}
 def serializable_lots(self):
  out=[]
  for x in self.resp_all:
   y=dict(x);y['paymentClocks']=sorted(y['paymentClocks']);out.append(y)
  return out

def physical_fields(r):
 keys=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations','twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','economicRepairQty','economicOverflowQty')
 return {k:r.get(k) for k in keys}
def eq(a,b):
 if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
 if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
 return a==b

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--offline-shadow');ap.add_argument('--output',required=True);a=ap.parse_args()
 mids=[int(x) for x in a.market_ids.split(',') if x.strip()];ref=json.loads(Path(a.reference).read_text(encoding='utf-8'));refrows={int(r['marketId']):r for r in ref['rows'] if r.get('cell')=='B_RECURSIVE_REPAIR_LADDER_V1'}
 off={}
 if a.offline_shadow:
  d=json.loads(Path(a.offline_shadow).read_text(encoding='utf-8'));off={int(x['marketId']):x for x in d.get('marketSummary',[])}
 rows=[]
 with tempfile.TemporaryDirectory(prefix='qty_ledger_shadow_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
  for mid in mids:
   sim=QuantityLedgerShadowSim(root/'tapes'/f'{mid}.json.xz',True)
   try:r=sim.run_ladder('__UNSCORED__')
   finally:sim.close()
   winner=str(co[mid]['winner']).upper();r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional'])
   parity={k:eq(physical_fields(r)[k],physical_fields(refrows[mid])[k]) for k in physical_fields(r)}
   ls=sim.ledger_summary();offsame=None
   if mid in off:
    o=off[mid];offsame={'responsibilitiesBorn':ls['responsibilitiesBorn']==int(o['responsibilitiesBorn']),'completed':ls['responsibilitiesCompleted']==int(o['completed']),
      'multiPayment':ls['multiPaymentCompleted']==int(o['multiPaymentCompleted']),'terminalOutstandingQty':math.isclose(ls['terminalOutstandingQty'],float(o['terminalOutstandingQty']),rel_tol=1e-10,abs_tol=1e-7)}
   row={'marketId':mid,'winnerPostHocOnly':winner,'behaviorParity':parity,'behaviorParityPassed':all(parity.values()),'ledgerSummary':ls,'offlineShadowParity':offsame,'responsibilities':sim.serializable_lots(),'paymentRows':sim.resp_payment_rows};rows.append(row)
   print(json.dumps({'marketId':mid,'behaviorParity':all(parity.values()),'ledger':ls,'offlineParity':offsame},ensure_ascii=False),flush=True)
 out={'version':'ETH_QUANTITY_RESPONSIBILITY_LEDGER_RUNTIME_SHADOW_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'markets':mids,'rows':rows,
  'allBehaviorParity':all(x['behaviorParityPassed'] for x in rows),'allLedgerInvariantsPass':all(not x['ledgerSummary']['invariantViolations'] for x in rows),
  'allOfflineShadowParity':all(x['offlineShadowParity'] is None or all(x['offlineShadowParity'].values()) for x in rows),
  'boundary':['frozen V1 action behavior unchanged','ledger shadow only','existing responsibility paid first FIFO','same-clock direct pair before residual birth','new residual cannot pay itself at same clock','exact remaining quantity','no winner/PnL action authority','no 8781']}
 v1.write_json(a.output,out);print(json.dumps({'ok':True,'allBehaviorParity':out['allBehaviorParity'],'allLedgerInvariantsPass':out['allLedgerInvariantsPass'],'allOfflineShadowParity':out['allOfflineShadowParity']},ensure_ascii=False))
if __name__=='__main__':main()
