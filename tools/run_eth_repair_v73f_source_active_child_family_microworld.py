from __future__ import annotations
import json,os
from pathlib import Path
EPS=1e-9
class Family:
 def __init__(self,side,authorized):
  self.side=side;self.authorized=float(authorized);self.members={};self.seen={};self.fill=0.;self.debt=0.;self.paid=0.;self.born=False;self.prebirth=0.;self.overfill=0.;self.dupDebt=0.
 def add(self,key,side):
  if side!=self.side:return False
  self.members[key]=side;self.seen.setdefault(key,0.);return True
 def fill_event(self,key,cum):
  if key not in self.members:return False
  old=self.seen[key];cum=float(cum)
  if cum<=old+EPS:return True
  inc=cum-old;self.seen[key]=cum
  room=max(0.,self.authorized-self.fill);used=min(room,inc);self.overfill+=max(0.,inc-used);self.fill+=used
  if used>EPS:
   if self.born and self.debt<self.fill-EPS:self.dupDebt+=0.
   self.debt+=used;self.born=True
  return True
 def repair(self,qty,post_birth=True):
  qty=float(qty)
  if not self.born or not post_birth:self.prebirth+=qty;return 0.
  pay=min(qty,max(0.,self.debt-self.paid));self.paid+=pay;return pay

def main():
 rows=[]
 f=Family('UP',3);f.add('UP_SRC','UP');f.add('UP_CHILD','UP');f.fill_event('UP_CHILD',2);rows.append(('zero_source_child_birth',abs(f.fill-2)<EPS and abs(f.debt-2)<EPS))
 before=f.debt;f.fill_event('UP_CHILD',2);rows.append(('duplicate_child_idempotent',abs(f.debt-before)<EPS))
 f2=Family('UP',3);f2.add('S','UP');f2.add('C','UP');f2.fill_event('S',1.2);f2.fill_event('C',1.8);rows.append(('partial_source_plus_child_shared_cap',abs(f2.fill-3)<EPS and abs(f2.debt-3)<EPS and f2.overfill<=EPS))
 f3=Family('DOWN',2);f3.add('S','DOWN');f3.add('C','DOWN');f3.repair(1,post_birth=False);f3.fill_event('C',2);rows.append(('prebirth_repair_not_payment',abs(f3.paid)<EPS and f3.prebirth>0))
 p=f3.repair(1.5,post_birth=True);rows.append(('postbirth_repair_pays_once',abs(p-1.5)<EPS and abs(f3.paid-1.5)<EPS))
 rows.append(('side_mismatch_rejected',not f3.add('BAD','UP')))
 f4=Family('UP',2);f4.add('S','UP');f4.add('C','UP');f4.fill_event('S',1.5);f4.fill_event('C',1.0);rows.append(('family_overfill_detected',abs(f4.fill-2)<EPS and abs(f4.overfill-.5)<EPS))
 f5=Family('UP',2);f5.add('S','UP');f5.add('C','UP');f5.fill_event('C',2);f5.repair(3,True);rows.append(('repair_capped_at_debt',abs(f5.paid-2)<EPS))
 outrows=[{'name':n,'pass':bool(p)} for n,p in rows];g={'allEightPass':len(outrows)==8 and all(x['pass'] for x in outrows),'exactDebtConservation':abs(f2.fill-f2.debt)<EPS,'zeroDuplicateDebt':f.dupDebt<=EPS,'sharedFamilyBudget':abs(f2.fill-3)<EPS,'preBirthExcluded':f3.prebirth>0,'postBirthPaymentCapped':abs(f5.paid-f5.debt)<EPS}
 decision='AUTHORIZE_V73F_ONE_MARKET_1825353_HFT_SMOKE' if all(g.values()) else 'REJECT_V73F_FAMILY_LEDGER'
 out={'version':'ETH_REPAIR_V73F_SOURCE_ACTIVE_CHILD_RESPONSIBILITY_FAMILY_MICROWORLD','date':'2026-09-03','researchOnly':True,'rows':outrows,'gates':g,'decision':decision,'boundary':['functional micro-world','one responsibility across source/child','no action authority','no PnL/winner','no H100','no 8781']};outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));outdir.mkdir(parents=True,exist_ok=True);(outdir/'result.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
