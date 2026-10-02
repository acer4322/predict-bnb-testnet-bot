from __future__ import annotations
import json
from pathlib import Path
EPS=1e-9
OUT=Path('data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V89A_MANAGER_DEBT_EXECUTION_TRANCHE_MICROWORLD_20260903.json')

class Ledger:
 def __init__(self,debt,side='UP',born=1000):
  self.managerDebt=float(debt);self.side=side;self.repairPaid=0.;self.overflowDebt=0.;self.overflowPaid=0.;self.overflowBornAt=None;self.fillSeen={};self.generationBirths=0
 def alloc_fill(self,key,cum_fill,t,allow_new_exposure=True):
  old=self.fillSeen.get(key,0.);inc=max(0.,float(cum_fill)-old);self.fillSeen[key]=max(old,float(cum_fill))
  rem=max(0.,self.managerDebt-self.repairPaid);repair=min(inc,rem);overflow=max(0.,inc-repair)
  if overflow>EPS and not allow_new_exposure:
   # In runtime this carrier must be prevented/capped before submit; micro-world marks invalid attempted overflow.
   return {'inc':inc,'repair':repair,'overflow':overflow,'allowed':False}
  self.repairPaid+=repair
  if overflow>EPS:
   if self.overflowBornAt is None:
    self.overflowBornAt=int(t);self.generationBirths+=1
   self.overflowDebt+=overflow
  return {'inc':inc,'repair':repair,'overflow':overflow,'allowed':True}
 def pay_overflow(self,cum_repair,obs_key,t):
  # Strictly post-birth increments only.
  old=self.fillSeen.get(obs_key,0.);inc=max(0.,float(cum_repair)-old);self.fillSeen[obs_key]=max(old,float(cum_repair))
  if self.overflowBornAt is None or int(t)<=int(self.overflowBornAt):return 0.
  rem=max(0.,self.overflowDebt-self.overflowPaid);pay=min(inc,rem);self.overflowPaid+=pay;return pay

def ck(name,cond,detail=None):return {'name':name,'pass':bool(cond),'detail':detail}

def main():
 cases=[]
 l=Ledger(5);r=l.alloc_fill('m',2,1100);cases.append(ck('below_debt_partial_repair',r['repair']==2 and r['overflow']==0 and l.repairPaid==2))
 l=Ledger(5);r=l.alloc_fill('m',5,1100);cases.append(ck('equal_debt_settlement_no_overflow',abs(r['repair']-5)<EPS and r['overflow']<EPS and l.generationBirths==0))
 l=Ledger(5);r=l.alloc_fill('m',8,1100);cases.append(ck('above_debt_composite',abs(r['repair']-5)<EPS and abs(r['overflow']-3)<EPS and abs(l.overflowDebt-3)<EPS and l.generationBirths==1))
 l=Ledger(5);a=l.alloc_fill('m',3,1100);b=l.alloc_fill('m',6,1200);cases.append(ck('partial_crossing_only_incremental_overflow',a['overflow']<EPS and abs(b['repair']-2)<EPS and abs(b['overflow']-1)<EPS and abs(l.overflowDebt-1)<EPS))
 before=(l.repairPaid,l.overflowDebt,l.generationBirths);d=l.alloc_fill('m',6,1300);after=(l.repairPaid,l.overflowDebt,l.generationBirths);cases.append(ck('duplicate_fill_idempotent',before==after and d['inc']<EPS))
 # Replacement carrier shares same manager debt; only confirmed fills allocate.
 l=Ledger(5);a=l.alloc_fill('old',2,1100);b=l.alloc_fill('new',4,1200);cases.append(ck('replacement_same_responsibility_budget',abs(l.repairPaid-5)<EPS and abs(l.overflowDebt-1)<EPS and l.generationBirths==1,{'old':a,'new':b}))
 # Route neutrality.
 lm=Ledger(2);lt=Ledger(2);rm=lm.alloc_fill('maker',3,1100);rt=lt.alloc_fill('taker',3,1100);cases.append(ck('maker_taker_same_allocation_semantics',rm['repair']==rt['repair']==2 and rm['overflow']==rt['overflow']==1))
 # One overflow responsibility for repeated partials in same carrier/generation.
 l=Ledger(2);l.alloc_fill('m',3,1100);l.alloc_fill('m',4,1200);cases.append(ck('one_overflow_responsibility_per_generation',l.generationBirths==1 and abs(l.overflowDebt-2)<EPS))
 # Strictly post-birth payment.
 l=Ledger(2);l.fillSeen['opp']=1.5;l.alloc_fill('m',3,1100);pre=l.pay_overflow(2.0,'opp',1100);post=l.pay_overflow(2.5,'opp',1200);cases.append(ck('strict_post_birth_payment',pre==0 and abs(post-.5)<EPS and abs(l.overflowPaid-.5)<EPS))
 # Pre-birth baseline cannot leak.
 l=Ledger(2);l.fillSeen['opp']=4.;l.alloc_fill('m',3,1100);pay=l.pay_overflow(4.,'opp',1200);cases.append(ck('prebirth_repair_cannot_pay_future_overflow',pay==0 and l.overflowPaid==0))
 # <=180: attempted crossing invalid; pure repair is allowed.
 l=Ledger(2);bad=l.alloc_fill('m',3,1100,allow_new_exposure=False);l2=Ledger(3);good=l2.alloc_fill('m',2,1100,allow_new_exposure=False);cases.append(ck('late_window_blocks_new_overflow_only',not bad['allowed'] and good['allowed'] and good['overflow']==0))
 # Conservation and repair bound over varied examples.
 conservation=True;bound=True
 for debt,qty in [(0.4,2.5),(1,1),(3,7),(10,5),(2.2,2.9)]:
  l=Ledger(debt);r=l.alloc_fill('x',qty,1100);conservation&=abs(r['repair']+r['overflow']-r['inc'])<1e-8;bound&=r['repair']<=debt+EPS
 cases.append(ck('physical_allocation_conservation',conservation));cases.append(ck('repair_never_exceeds_manager_debt',bound))
 passed=sum(x['pass'] for x in cases);out={'version':'TARGET_ETH_V89A_MANAGER_DEBT_EXECUTION_TRANCHE_MICROWORLD','date':'2026-09-03','researchOnly':True,'cases':cases,'passed':passed,'total':len(cases),'functionalPass':passed==len(cases),'interpretation':'Manager debt is an allocation cap, not physical order size. Execution carrier can be larger; confirmed overflow becomes a new responsibility under one generation identity.'};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
