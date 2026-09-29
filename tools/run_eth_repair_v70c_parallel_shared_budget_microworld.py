from __future__ import annotations
import argparse,json,importlib.util,sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
p=HERE/'run_eth_repair_v70_composite_carrier_ledger_microworld.py'
s=importlib.util.spec_from_file_location('v70base',p);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
CompositeLedger=m.CompositeLedger;CompositeCarrier=m.CompositeCarrier;EPS=m.EPS

class ParallelLedger(CompositeLedger):
    def submit_role(self,carrier_id,lane,side,qty,venue_min_qty,remaining_sec,role,expand_objective_id=None):
        qty=float(qty); role=str(role).upper()
        if carrier_id in self.carriers or lane not in ('PASSIVE','ACTIVE') or side not in ('UP','DOWN') or role not in ('REPAIR','EXPAND','COMPOSITE'):
            return self._block('INVALID_CARRIER',carrierId=carrier_id)
        if qty+EPS<float(venue_min_qty):return self._block('VENUE_MIN',carrierId=carrier_id)
        if role in ('EXPAND','COMPOSITE') and remaining_sec<=180.0+EPS:
            # COMPOSITE may still be Repair-only if qty never reaches overflow; checked below after allocation.
            if role=='EXPAND':return self._block('TIME_CUTOFF_NO_NEW_EXPOSURE',carrierId=carrier_id)

        repairs=sorted((o for o in self.repairs.values() if o.side==side and o.available_qty>EPS),key=lambda o:(o.born_seq,o.obligation_id))
        repair_available=sum(o.available_qty for o in repairs)
        repair_authorized=0.0; expand_authorized=0.0; objective=None
        if role=='REPAIR':
            repair_authorized=min(qty,repair_available)
            if repair_authorized+EPS<qty:return self._block('REPAIR_SHARED_BUDGET',carrierId=carrier_id,requestedQty=qty,availableQty=repair_available)
        elif role=='EXPAND':
            if expand_objective_id is None or expand_objective_id not in self.expands:return self._block('MISSING_EXPAND_AUTHORIZATION',carrierId=carrier_id)
            objective=self.expands[expand_objective_id]
            if objective.side!=side:return self._block('EXPAND_SIDE_MISMATCH',carrierId=carrier_id)
            if self.latest_expand_by_side.get(side)!=expand_objective_id:return self._block('SUPERSEDED_EXPAND_OBJECTIVE',carrierId=carrier_id)
            if objective.available_qty+EPS<qty:return self._block('EXPAND_SHARED_BUDGET',carrierId=carrier_id,requestedQty=qty,availableQty=objective.available_qty)
            expand_authorized=qty
        else:
            repair_authorized=min(qty,repair_available); expand_authorized=max(0.0,qty-repair_authorized)
            if repair_authorized<=EPS:return self._block('NO_REPAIR_RESPONSIBILITY',carrierId=carrier_id)
            if remaining_sec<=180.0+EPS and expand_authorized>EPS:return self._block('TIME_CUTOFF_NO_NEW_EXPOSURE',carrierId=carrier_id)
            if expand_authorized>EPS:
                if expand_objective_id is None or expand_objective_id not in self.expands:return self._block('MISSING_EXPAND_AUTHORIZATION',carrierId=carrier_id)
                objective=self.expands[expand_objective_id]
                if objective.side!=side:return self._block('EXPAND_SIDE_MISMATCH',carrierId=carrier_id)
                if self.latest_expand_by_side.get(side)!=expand_objective_id:return self._block('SUPERSEDED_EXPAND_OBJECTIVE',carrierId=carrier_id)
                if objective.available_qty+EPS<expand_authorized:return self._block('EXPAND_SHARED_BUDGET',carrierId=carrier_id,requestedQty=expand_authorized,availableQty=objective.available_qty)

        left=repair_authorized; reservations=[]
        for o in repairs:
            take=min(left,o.available_qty)
            if take<=EPS:continue
            o.reserved_qty+=take;reservations.append({'obligationId':o.obligation_id,'remainingReservedQty':take});left-=take
            if left<=EPS:break
        if left>EPS:raise AssertionError('repair reservation underflow')
        if objective is not None and expand_authorized>EPS:objective.reserved_qty+=expand_authorized
        c=CompositeCarrier(carrier_id=carrier_id,lane=lane,side=side,submitted_qty=qty,repair_reservations=reservations,expand_objective_id=expand_objective_id if expand_authorized>EPS else None,expand_reserved_qty=expand_authorized,remaining_sec=float(remaining_sec))
        self.carriers[carrier_id]=c
        self.events.append({'event':'PARALLEL_SHARED_BUDGET_SUBMIT','carrierId':carrier_id,'lane':lane,'side':side,'role':role,'submittedQty':qty,'repairAuthorizedQty':repair_authorized,'expandAuthorizedQty':expand_authorized,'expandObjectiveId':c.expand_objective_id})
        return {'ok':True,'carrierId':carrier_id,'repairAuthorizedQty':repair_authorized,'expandAuthorizedQty':expand_authorized}

def ok_audit(a):
    return all([a['physicalConservation'],a['generationDebtExact'],a['nonnegativeReservations'],a['responsibilityWithinBounds'],a['expandBudgetWithinBounds'],a['overOwned']==0,a['truthMismatch']==0,a['unauthorizedRoleDrift']==0])

def scenarios():
    out=[]
    # 1: Passive + Active pay disjoint Repair quotas from one responsibility.
    x=ParallelLedger();x.add_repair('R1','UP',10,1)
    a=x.submit_role('P1','PASSIVE','UP',6,1,240,'REPAIR');b=x.submit_role('A1','ACTIVE','UP',4,1,240,'REPAIR');x.fill('A1','F_A',4);x.fill('P1','F_P',6)
    out.append(('PARALLEL_REPAIR_QUOTAS',a['ok'] and b['ok'] and abs(x.repairs['R1'].paid_qty-10)<EPS,x.audit()))
    # 2: Combined live quotas cannot overbook Repair.
    x=ParallelLedger();x.add_repair('R1','DOWN',10,1);x.submit_role('P1','PASSIVE','DOWN',7,1,240,'REPAIR');b=x.submit_role('A1','ACTIVE','DOWN',4,1,240,'REPAIR')
    out.append(('REPAIR_OVERBOOK_BLOCKED',not b['ok'] and b['reason']=='REPAIR_SHARED_BUDGET',x.audit()))
    # 3: Passive + Active share one Expand budget concurrently.
    x=ParallelLedger();x.add_expand('E1','UP',10,1);a=x.submit_role('P1','PASSIVE','UP',6,1,240,'EXPAND','E1');b=x.submit_role('A1','ACTIVE','UP',4,1,240,'EXPAND','E1');x.fill('P1','FP',6);x.fill('A1','FA',4)
    out.append(('PARALLEL_EXPAND_QUOTAS',a['ok'] and b['ok'] and abs(x.expands['E1'].paid_qty-10)<EPS and abs(x.audit()['generationDebtQty']-10)<EPS,x.audit()))
    # 4: Combined live quotas cannot overbook Expand.
    x=ParallelLedger();x.add_expand('E1','DOWN',8,1);x.submit_role('P1','PASSIVE','DOWN',5,1,240,'EXPAND','E1');b=x.submit_role('A1','ACTIVE','DOWN',4,1,240,'EXPAND','E1')
    out.append(('EXPAND_OVERBOOK_BLOCKED',not b['ok'] and b['reason']=='EXPAND_SHARED_BUDGET',x.audit()))
    # 5: Repair and Expand lanes may coexist as separate allocations under one side without double-spend.
    x=ParallelLedger();x.add_repair('R1','UP',5,1);x.add_expand('E1','UP',5,2);a=x.submit_role('P1','PASSIVE','UP',5,1,240,'REPAIR');b=x.submit_role('A1','ACTIVE','UP',5,1,240,'EXPAND','E1');x.fill('A1','FA',5);x.fill('P1','FP',5)
    au=x.audit();out.append(('REPAIR_AND_EXPAND_CONCURRENT',a['ok'] and b['ok'] and abs(au['repairPaidQty']-5)<EPS and abs(au['expandPaidQty']-5)<EPS,out[-1][2] if False else au))
    # 6: Terminal release allows replacement carrier to take released quota.
    x=ParallelLedger();x.add_repair('R1','DOWN',10,1);x.submit_role('P1','PASSIVE','DOWN',6,1,240,'REPAIR');x.terminal('P1');b=x.submit_role('A1','ACTIVE','DOWN',10,1,240,'REPAIR');x.fill('A1','FA',10)
    out.append(('TERMINAL_RELEASE_REUSES_BUDGET',b['ok'] and abs(x.repairs['R1'].paid_qty-10)<EPS,x.audit()))
    # 7: <=180 allows Repair quota, forbids Expand quota.
    x=ParallelLedger();x.add_repair('R1','UP',4,1);x.add_expand('E1','UP',4,2);a=x.submit_role('P1','PASSIVE','UP',4,1,180,'REPAIR');b=x.submit_role('A1','ACTIVE','UP',4,1,180,'EXPAND','E1');x.fill('P1','FP',4)
    out.append(('TIME_CUTOFF_REPAIR_ONLY',a['ok'] and (not b['ok']) and b['reason']=='TIME_CUTOFF_NO_NEW_EXPOSURE',x.audit()))
    # 8: Duplicate execution id cannot double-pay shared budgets/generation.
    x=ParallelLedger();x.add_expand('E1','DOWN',6,1);x.submit_role('A1','ACTIVE','DOWN',6,1,240,'EXPAND','E1');x.fill('A1','VENUE1',6);before=x.audit();x.fill('A1','VENUE1',6);after=x.audit()
    out.append(('DUPLICATE_FILL_IDEMPOTENT',after['duplicateFillIgnored']==1 and abs(before['expandPaidQty']-after['expandPaidQty'])<EPS and abs(before['generationDebtQty']-after['generationDebtQty'])<EPS,after))
    # 9: Superseded Expand objective cannot retain execution authority.
    x=ParallelLedger();x.add_expand('E_OLD','UP',5,1);x.add_expand('E_NEW','UP',5,2);b=x.submit_role('A1','ACTIVE','UP',2,1,240,'EXPAND','E_OLD')
    out.append(('SUPERSEDED_EXPAND_BLOCKED',not b['ok'] and b['reason']=='SUPERSEDED_EXPAND_OBJECTIVE',x.audit()))
    # 10: Active Repair sizing derives from responsibility, not Maker 10/18 cap.
    x=ParallelLedger();x.add_repair('R1','DOWN',25,1);a=x.submit_role('A1','ACTIVE','DOWN',25,1,240,'REPAIR');x.fill('A1','FA',25)
    out.append(('ACTIVE_RESPONSIBILITY_SIZING',a['ok'] and a['repairAuthorizedQty']==25 and x.repairs['R1'].paid_qty==25,x.audit()))
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);a=ap.parse_args();rows=[]
    for name,semantic,audit in scenarios():rows.append({'scenario':name,'semanticPass':bool(semantic),'accountingPass':ok_audit(audit),'passed':bool(semantic and ok_audit(audit)),'audit':audit})
    gates={'allTenPassed':len(rows)==10 and all(r['passed'] for r in rows),'physicalConservation':all(r['audit']['physicalConservation'] for r in rows),'generationDebtExact':all(r['audit']['generationDebtExact'] for r in rows),'zeroResponsibilityOverfill':all(r['audit']['responsibilityWithinBounds'] for r in rows),'zeroExpandOverfill':all(r['audit']['expandBudgetWithinBounds'] for r in rows),'zeroUnauthorizedRoleDrift':all(r['audit']['unauthorizedRoleDrift']==0 for r in rows),'duplicateIdempotence':any(r['audit']['duplicateFillIgnored']==1 for r in rows)}
    report={'version':'ETH_REPAIR_V70C_PARALLEL_SHARED_BUDGET_MICROWORLD','date':'2026-09-03','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'hypothesis':'Passive and Active carriers may coexist as disjoint reservations of one Repair/Expand responsibility budget; fill/cancel events conserve budget and generation debt.','gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['Pure ledger micro-world only; no HFT/fill assumption.','No terminal-failure requirement for Active quota.','Passive+Active may coexist only through explicit non-overlapping reservations.','Repair/Expand management responsibility remains separate from execution lane.','<=180s forbids new Expand allocation.','No threshold/qty/delay tuning, no PnL/winner, no H100, no 8781.','Pass authorizes only 1-3 market functional reachability smoke.']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':report['functionalPass'],'gates':gates,'output':str(op)},ensure_ascii=False))
if __name__=='__main__':main()
