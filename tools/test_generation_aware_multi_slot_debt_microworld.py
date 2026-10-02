from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
EPS=1e-9

def close(a,b,tol=1e-9):return abs(float(a)-float(b))<=tol

def main():
    L=GenerationAwareSharedParentDebtAllocationLedgerV3();rows=[]
    # First generation establishes parent debt 2.0 and pays 0.75.
    a=L.allocate_cumulative('P1',11,0.75,2.0)
    s1=L.describe_parent(11)
    rows.append({'case':'first_partial_payment','pass':close(s1['repairPaid'],0.75) and close(s1['remainingDebt'],1.25),'state':s1})
    # Second slot confirmed fill adds 1.5 debt; prior repairPaid must survive.
    g2=L.attach_generation_debt(11,'slot2:epoch1',1.5);s2=L.describe_parent(11)
    rows.append({'case':'slot2_attach_preserves_paid','pass':g2.applied and close(s2['initialDebt'],3.5) and close(s2['remainingDebt'],2.75) and close(s2['repairPaid'],0.75),'state':s2,'attach':g2.__dict__})
    # Another physical Repair payment crosses one share.
    b=L.allocate_cumulative('P2',11,1.0,2.75);s3=L.describe_parent(11)
    rows.append({'case':'interleaved_repair_payment','pass':close(s3['repairPaid'],1.75) and close(s3['remainingDebt'],1.75),'state':s3})
    # Third slot adds 0.8 debt after payment; again paid amount must not reset.
    g3=L.attach_generation_debt(11,'slot3:epoch1',0.8);s4=L.describe_parent(11)
    rows.append({'case':'slot3_attach_after_payment','pass':g3.applied and close(s4['initialDebt'],4.3) and close(s4['remainingDebt'],2.55) and close(s4['repairPaid'],1.75),'state':s4,'attach':g3.__dict__})
    # Duplicate attach token is idempotent.
    dup=L.attach_generation_debt(11,'slot3:epoch1',0.8);s5=L.describe_parent(11)
    rows.append({'case':'duplicate_slot3_idempotent','pass':not dup.applied and close(s5['initialDebt'],4.3) and close(s5['remainingDebt'],2.55),'state':s5,'attach':dup.__dict__})
    # Finish remaining debt through two sibling carriers; physical conservation must hold.
    c=L.allocate_cumulative('A1',11,1.5,2.55);d=L.allocate_cumulative('A2',11,1.05,1.05);s6=L.describe_parent(11)
    cons=all(close(x.fill_increment,x.repair_increment+x.overflow_increment) for x in L.allocations)
    rows.append({'case':'finish_all_generations','pass':close(s6['remainingDebt'],0.0) and close(s6['repairPaid'],4.3) and cons,'state':s6,'physicalConservation':cons})
    # Independent parent cannot contaminate parent11.
    L.allocate_cumulative('Q1',12,0.5,1.2);s7=L.describe_parent(11);q=L.describe_parent(12)
    rows.append({'case':'parent_isolation','pass':close(s7['initialDebt'],4.3) and close(q['remainingDebt'],0.7),'parent11':s7,'parent12':q})
    out={'version':'GENERATION_AWARE_MULTI_SLOT_DEBT_MICROWORLD_V1','date':'2026-09-05','checks':{r['case']:bool(r['pass']) for r in rows},'allPass':all(r['pass'] for r in rows),'rows':rows,'attachments':L.describe_generation_attachments(),'boundary':['allocation semantics only','no order submit','no Target runtime input','no 8781']}
    print(json.dumps(out,indent=2))
if __name__=='__main__':main()
