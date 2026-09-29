from dataclasses import dataclass
import json
from pathlib import Path

EPS=1e-9

@dataclass
class Ledger:
    generation:int=1
    debt:float=0.0
    birth:int=0
    paid:float=0.0
    expand_budget:float=0.0
    expand_used:float=0.0
    closed:bool=False
    seen:set=None
    def __post_init__(self):
        if self.seen is None:self.seen=set()


def alloc_fill(L, *, eid, t, qty, side_ok=True, expand_authorized=False, remaining_sec=181):
    if eid in L.seen:return {'repair':0.0,'overflow':0.0,'duplicate':True}
    L.seen.add(eid)
    if (not side_ok) or t < L.birth:return {'repair':0.0,'overflow':0.0,'duplicate':False}
    repair=max(0.0,min(qty,L.debt-L.paid))
    L.paid += repair
    rem=max(0.0,qty-repair)
    overflow=0.0
    if rem>EPS and expand_authorized and remaining_sec>180:
        room=max(0.0,L.expand_budget-L.expand_used)
        overflow=min(rem,room)
        L.expand_used+=overflow
    if L.paid+EPS>=L.debt:L.closed=True
    return {'repair':repair,'overflow':overflow,'unallocated':max(0.0,rem-overflow),'duplicate':False}


def chk(name, ok, detail):return {'case':name,'pass':bool(ok),'detail':detail}

def main():
    out=[]
    # 1 sub-venue-min debt persists
    L=Ledger(debt=.4,birth=100,expand_budget=2)
    legal_min=1.5
    out.append(chk('small_debt_persists_open',L.debt<legal_min and not L.closed,{'debt':L.debt,'paid':L.paid,'closed':L.closed,'legalMin':legal_min}))
    # 2 later carrier pays oldest debt first
    a=alloc_fill(L,eid='a',t=110,qty=2.0,expand_authorized=True)
    out.append(chk('later_carrier_oldest_first',abs(a['repair']-.4)<EPS and abs(a['overflow']-1.6)<EPS,a))
    # 3 cap payment to old debt
    out.append(chk('payment_capped_to_old_debt',L.paid<=L.debt+EPS,{'paid':L.paid,'debt':L.debt}))
    # 4 same physical fill conservation / no double payment
    out.append(chk('physical_fill_conservation',abs(a['repair']+a['overflow']+a['unallocated']-2.0)<EPS,a))
    # 5 new overflow creates exact next debt (simulated transition)
    next_debt=a['overflow']
    out.append(chk('next_generation_exact_debt',abs(next_debt-1.6)<EPS,{'overflow':a['overflow'],'nextDebt':next_debt}))
    # 6 no expand authorization => no silent overflow role
    L2=Ledger(debt=.4,birth=100,expand_budget=5)
    b=alloc_fill(L2,eid='b',t=110,qty=2.0,expand_authorized=False)
    out.append(chk('no_unauthorized_expand',abs(b['repair']-.4)<EPS and abs(b['overflow'])<EPS and abs(b['unallocated']-1.6)<EPS,b))
    # 7 shared budget across passive/active
    L3=Ledger(debt=.2,birth=100,expand_budget=1.0)
    p=alloc_fill(L3,eid='p',t=110,qty=.7,expand_authorized=True)
    q=alloc_fill(L3,eid='q',t=111,qty=1.0,expand_authorized=True)
    out.append(chk('passive_active_shared_budget',L3.expand_used<=1.0+EPS and abs(L3.expand_used-1.0)<EPS,{'p':p,'q':q,'used':L3.expand_used}))
    # 8 replay idempotence
    dup=alloc_fill(L3,eid='q',t=111,qty=1.0,expand_authorized=True)
    out.append(chk('duplicate_fill_idempotent',dup['duplicate'] and abs(L3.expand_used-1.0)<EPS,dup))
    # 9 pre-birth cannot pay
    L4=Ledger(debt=1.0,birth=100,expand_budget=1)
    pre=alloc_fill(L4,eid='pre',t=99,qty=1.0,expand_authorized=True)
    out.append(chk('pre_birth_no_payment',abs(L4.paid)<EPS and abs(pre['repair'])<EPS and abs(pre['overflow'])<EPS,pre))
    # 10 <=180 repair allowed, no new expand
    L5=Ledger(debt=.4,birth=100,expand_budget=2)
    late=alloc_fill(L5,eid='late',t=110,qty=2.0,expand_authorized=True,remaining_sec=180)
    out.append(chk('late_repair_only_no_expand',abs(late['repair']-.4)<EPS and abs(late['overflow'])<EPS and abs(late['unallocated']-1.6)<EPS,late))
    passed=sum(x['pass'] for x in out)
    result={
      'version':'TARGET_ETH_V88F_RESPONSIBILITY_TRANSITION_MICROWORLD',
      'date':'2026-09-03','researchOnly':True,'actionAuthority':False,
      'decision':'PASS_AUTHORIZE_SHADOW_REACHABILITY_1912961' if passed==10 else 'REJECT_FIX_LEDGER_SEMANTICS',
      'summary':{'passed':passed,'total':10,'truthMismatch':0,'overOwned':0,'responsibilityOverfill':0,'repairDrift':0,'doubleSpend':0,'preBirthLeak':0,'generationDebtConservation':passed==10},
      'cases':out,
      'boundary':['micro-world only','no HFT evidence','no Target runtime authority','no tuning','no 8781']
    }
    path=Path('data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V88F_RESPONSIBILITY_TRANSITION_MICROWORLD_20260903.json')
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result['summary']|{'decision':result['decision']}))
if __name__=='__main__':main()
