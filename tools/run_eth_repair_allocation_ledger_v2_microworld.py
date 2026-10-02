from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from allocation_ledger_v2 import SharedParentDebtAllocationLedgerV2,EPS

def ok(name,cond,**extra):return {'case':name,'ok':bool(cond),**extra}
def main():
 rows=[]
 # 1 partial single carrier
 L=SharedParentDebtAllocationLedgerV2();r=L.allocate_cumulative('A',1,2.0,5.0);rows.append(ok('partial',r.repair_increment==2 and r.overflow_increment==0 and abs(L.remaining(1)-3)<EPS,result=r.__dict__))
 # 2 crossing single carrier
 L=SharedParentDebtAllocationLedgerV2();r=L.allocate_cumulative('A',1,7.0,5.0);rows.append(ok('crossing',abs(r.repair_increment-5)<EPS and abs(r.overflow_increment-2)<EPS and L.remaining(1)<=EPS,result=r.__dict__))
 # 3 sibling passive then active: second becomes pure overflow
 L=SharedParentDebtAllocationLedgerV2();a=L.allocate_cumulative('P',10,2.04,1.2883051451619956);b=L.allocate_cumulative('A',10,1.36986301369863,0.7516948548380036);rows.append(ok('siblings_passive_then_active',abs(a.repair_increment-1.2883051451619956)<1e-9 and abs(a.overflow_increment-0.7516948548380044)<1e-8 and b.repair_increment<=EPS and abs(b.overflow_increment-1.36986301369863)<1e-9 and L.remaining(10)<=EPS,parent=L.describe_parent(10)))
 # 4 sibling active then passive uses same shared debt
 L=SharedParentDebtAllocationLedgerV2();a=L.allocate_cumulative('A',2,1.0,1.5);b=L.allocate_cumulative('P',2,1.0,1.5);rows.append(ok('siblings_active_then_passive',abs(a.repair_increment-1)<EPS and abs(b.repair_increment-.5)<EPS and abs(b.overflow_increment-.5)<EPS,parent=L.describe_parent(2)))
 # 5 partial cumulative idempotence
 L=SharedParentDebtAllocationLedgerV2();a=L.allocate_cumulative('P',3,.4,1.0);z=L.allocate_cumulative('P',3,.4,1.0);b=L.allocate_cumulative('P',3,.9,1.0);rows.append(ok('cumulative_idempotence',z is None and abs(b.fill_increment-.5)<EPS and abs(L.remaining(3)-.1)<EPS,parent=L.describe_parent(3)))
 # 6 later sibling cannot reset debt upward
 L=SharedParentDebtAllocationLedgerV2();L.allocate_cumulative('P',4,.8,1.0);L.register_carrier('A',4,9.0);rows.append(ok('sibling_cannot_reset_debt',abs(L.remaining(4)-.2)<EPS,parent=L.describe_parent(4)))
 # 7 multiple sibling overflow aggregates one transition bucket
 L=SharedParentDebtAllocationLedgerV2();L.allocate_cumulative('P',5,2.0,1.0);L.allocate_cumulative('A',5,3.0,0.0);rows.append(ok('aggregate_transition_overflow',abs(L.transition_overflow[5]-4.0)<EPS and abs(L.describe_parent(5)['overflowBorn']-4.0)<EPS,parent=L.describe_parent(5)))
 # 8 independent parents isolated
 L=SharedParentDebtAllocationLedgerV2();L.allocate_cumulative('A',6,.5,1.0);L.allocate_cumulative('B',7,.25,2.0);rows.append(ok('independent_parents',abs(L.remaining(6)-.5)<EPS and abs(L.remaining(7)-1.75)<EPS))
 # 9 conservation over all allocations
 L=SharedParentDebtAllocationLedgerV2();L.allocate_cumulative('P',8,2.0,1.25);L.allocate_cumulative('A',8,1.0,.5);cons=all(abs(x.fill_increment-x.repair_increment-x.overflow_increment)<1e-9 for x in L.allocations);rows.append(ok('physical_conservation',cons))
 # 10 Repair never exceeds initial manager debt
 st=L.describe_parent(8);rows.append(ok('repair_never_exceeds_parent_debt',st['repairPaid']<=st['initialDebt']+EPS,parent=st))
 # 11 pure overflow after debt zero is explicitly represented
 L=SharedParentDebtAllocationLedgerV2();L.allocate_cumulative('P',9,1.0,1.0);b=L.allocate_cumulative('A',9,.7,0.0);rows.append(ok('pure_overflow_after_settlement',b.repair_increment<=EPS and abs(b.overflow_increment-.7)<EPS,result=b.__dict__))
 # 12 one parent identity across multiple carriers
 rows.append(ok('one_parent_identity',len(L.describe_parent(9)['carriers'])==2,parent=L.describe_parent(9)))
 # 13 no negative debt
 rows.append(ok('no_negative_debt',all(x.remaining_debt>=-EPS for x in L.parents.values())))
 out={'version':'ETH_REPAIR_ALLOCATION_LEDGER_V2_MICROWORLD','passed':sum(r['ok'] for r in rows),'total':len(rows),'functionalPass':all(r['ok'] for r in rows),'rows':rows,'boundary':['manager debt shared at parent scope','physical carrier independent of manager debt','confirmed fill Repair-first overflow-second','sibling carriers cannot reset debt','one transition-overflow bucket per parent','no runtime Target inputs']}
 Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.')+'/result.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':out['functionalPass'],'passed':out['passed'],'total':out['total']}))
if __name__=='__main__':main()
