from __future__ import annotations
import json,sys
EPS=1e-9

def alloc(fill_qty,gap_before):
    r=min(max(0.0,fill_qty),max(0.0,gap_before));o=max(0.0,fill_qty-r);return r,o

def hyp_floor(u,d,cost,side,price,qty):
    if side=='UP':u+=qty
    else:d+=qty
    cost+=price*qty
    return min(u,d)-cost

def main():
    cases=[]
    # 1. Canonical fresh 1912961 crossing.
    u,d,c=0.0,1.6666666666666667,1.0;p=.40;q=1/p;g=d-u;r,o=alloc(q,g);f0=min(u,d)-c;f1=hyp_floor(u,d,c,'UP',p,q)
    cases.append(('canonical_cross',abs(r-g)<=EPS and abs(o-.8333333333333333)<1e-8 and f1>f0+EPS and abs((r+o)-q)<=EPS))
    # 2. Non-crossing is ordinary Repair, no overflow responsibility.
    r2,o2=alloc(1.0,2.0);cases.append(('non_cross_no_overflow',abs(r2-1.0)<=EPS and o2<=EPS))
    # 3. Partial fills allocate Repair first, overflow only after residual is paid.
    gap=1.6666666666666667;ra=oa=0.0
    for inc in [1.0,1.5]:
        rr,oo=alloc(inc,max(0.0,gap-ra));ra+=rr;oa+=oo
    cases.append(('partial_fifo_allocation',abs(ra-gap)<1e-8 and abs(oa-.8333333333333333)<1e-8))
    # 4. Physical-fill conservation.
    cases.append(('physical_conservation',abs((ra+oa)-2.5)<1e-8))
    # 5. Overflow debt cannot be paid by pre-birth Repair.
    overflow=oa;pre_birth_opposite=9.0;paid=0.0;post_birth=[.3,.7]
    for inc in post_birth:paid+=min(inc,max(0.0,overflow-paid))
    cases.append(('strict_post_birth_payment',pre_birth_opposite>0 and abs(paid-overflow)<1e-8))
    # 6. Duplicate observation is idempotent when cumulative fill does not increase.
    seen=2.5;cum=2.5;inc=max(0.0,cum-seen);cases.append(('idempotent_duplicate_fill',inc<=EPS))
    # 7. New overflow is forbidden <=180s even if Repair portion is desired.
    seconds_left=170;crossing=o>EPS;authorized=not(crossing and seconds_left<=180);cases.append(('late_overflow_block',not authorized))
    # 8. A composite candidate must improve worst-case floor.
    badp=1.0;badq=1.0;badf=hyp_floor(0.0,1.0,1.0,'UP',badp,badq);cases.append(('floor_improvement_required',not(badf>-1.0+EPS)))
    out={'version':'ETH_REPAIR_V84B_COMPOSITE_REPAIR_LEDGER_MICROWORLD','cases':[{'name':n,'pass':bool(v)} for n,v in cases],'passed':sum(bool(v) for _,v in cases),'total':len(cases),'functionalPass':all(v for _,v in cases)}
    print(json.dumps(out,indent=2));
    if not out['functionalPass']:sys.exit(2)
if __name__=='__main__':main()
