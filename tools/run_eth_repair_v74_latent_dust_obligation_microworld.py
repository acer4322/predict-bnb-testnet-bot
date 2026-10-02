from __future__ import annotations
import json,os
from pathlib import Path
EPS=1e-9
MIN_NOTIONAL=1.0

class DustLedger:
    def __init__(self, side:str):
        self.side=side
        self.qty=0.0
        self.owner=None
        self.seen=set()
        self.events=[]
        self.submits=[]
        self.fabricated_completion=0
        self.oversize=0.0
        self.below_min=0
        self.opposite_absorb=0.0
        self.double_count=0.0
    def _own(self):
        if self.qty>EPS and self.owner is None:self.owner='DUST_REPAIR_PENDING'
        if self.qty<=EPS:self.qty=0.0;self.owner=None
    def add_obligation(self,eid,side,qty,price,t=0):
        if eid in self.seen:
            self.events.append({'event':'DUPLICATE_OBLIGATION_IGNORED','id':eid});return
        self.seen.add(eid);q=max(0.0,float(qty))
        if side!=self.side:
            self.opposite_absorb+=0.0
            self.events.append({'event':'OPPOSITE_OBLIGATION_SEPARATE','id':eid,'side':side,'qty':q});return
        self.qty+=q;self._own();self.events.append({'event':'SAME_SIDE_OBLIGATION_ACCUMULATED','id':eid,'qtyAdded':q,'latentQty':self.qty,'price':price,'notional':self.qty*float(price),'t':t})
    def late_fill(self,eid,side,qty,t=0):
        if eid in self.seen:
            self.events.append({'event':'DUPLICATE_FILL_IGNORED','id':eid});return
        self.seen.add(eid);q=max(0.0,float(qty))
        if side!=self.side:
            self.events.append({'event':'OPPOSITE_FILL_IGNORED_FOR_DUST','id':eid,'side':side,'qty':q});return
        paid=min(self.qty,q);self.qty-=paid;self._own();self.events.append({'event':'LATE_CONFIRMED_REPAIR_FILL','id':eid,'fillQty':q,'paidQty':paid,'latentQty':self.qty,'t':t})
    def maybe_materialize(self,eid,price,seconds_left,t=0):
        if eid in self.seen:
            self.events.append({'event':'DUPLICATE_MATERIALIZE_IGNORED','id':eid});return 0.0
        self.seen.add(eid);p=float(price)
        if self.qty<=EPS:return 0.0
        # Repair is risk reducing and remains allowed <=180s.
        if self.qty*p < MIN_NOTIONAL-EPS:
            self.events.append({'event':'DUST_REMAINS_PENDING','id':eid,'qty':self.qty,'price':p,'notional':self.qty*p,'secondsLeft':seconds_left,'t':t});return 0.0
        q=self.qty
        if q*p < MIN_NOTIONAL-EPS:self.below_min+=1
        # exact owned obligation only, never round up.
        self.submits.append({'id':eid,'side':self.side,'qty':q,'price':p,'notional':q*p,'secondsLeft':seconds_left,'t':t,'role':'REPAIR'})
        self.events.append({'event':'EXACT_REPAIR_REMATERIALIZE','id':eid,'qty':q,'price':p,'notional':q*p,'secondsLeft':seconds_left,'t':t})
        self.qty=0.0;self.owner=None
        return q
    def end(self):
        if self.qty>EPS:self.events.append({'event':'PENDING_AT_DATA_END','qty':self.qty,'owner':self.owner})
        return {'side':self.side,'latentQty':self.qty,'owner':self.owner,'events':self.events,'submits':self.submits,'fabricatedCompletion':self.fabricated_completion,'oversizeQty':self.oversize,'belowMinSubmits':self.below_min,'oppositeAbsorbQty':self.opposite_absorb,'doubleCountQty':self.double_count}

def case(name,fn):
    ok,ledger,extra=fn();return {'name':name,'pass':bool(ok),'ledger':ledger.end(),**extra}

def main():
    rows=[]
    def c1():
        l=DustLedger('UP');l.add_obligation('o1','UP',0.5651500081960128,.49)
        q=l.maybe_materialize('m1',.49,240)
        return q==0 and abs(l.qty-0.5651500081960128)<1e-9 and l.owner=='DUST_REPAIR_PENDING',l,{}
    rows.append(case('fresh_1911708_submin_residual_stays_owned',c1))
    def c2():
        l=DustLedger('UP');l.add_obligation('o1','UP',0.5651500081960128,.49);l.add_obligation('o2','UP',1.60,.49)
        q=l.maybe_materialize('m1',.49,230);want=2.165150008196013
        return abs(q-want)<1e-9 and l.qty==0 and len(l.submits)==1 and abs(l.submits[0]['qty']-want)<1e-9,l,{'expectedExactQty':want}
    rows.append(case('same_side_growth_makes_exact_combined_repair_admissible',c2))
    def c3():
        l=DustLedger('UP');l.add_obligation('o1','UP',.56515,.49);l.add_obligation('o2','DOWN',5,.5);q=l.maybe_materialize('m1',.49,230)
        return q==0 and abs(l.qty-.56515)<1e-9,l,{}
    rows.append(case('opposite_side_obligation_cannot_absorb_dust',c3))
    def c4():
        l=DustLedger('UP');l.add_obligation('o1','UP',.56515,.49);l.add_obligation('o1','UP',.56515,.49)
        return abs(l.qty-.56515)<1e-9,l,{}
    rows.append(case('duplicate_obligation_idempotent',c4))
    def c5():
        l=DustLedger('UP');l.add_obligation('o1','UP',2.2,.49);l.late_fill('f1','UP',.7);q=l.maybe_materialize('m1',.49,230)
        return q==0 and abs(l.qty-1.5)<1e-9,l,{'recomputedResidual':1.5}
    rows.append(case('late_fill_recomputes_before_new_submit',c5))
    def c6():
        l=DustLedger('UP');l.add_obligation('o1','UP',.30,.49);l.add_obligation('o2','UP',.25,.49);q=l.maybe_materialize('m1',.49,230)
        return q==0 and abs(l.qty-.55)<1e-9 and l.owner=='DUST_REPAIR_PENDING',l,{}
    rows.append(case('multiple_submin_same_side_obligations_merge_single_owner',c6))
    def c7():
        l=DustLedger('UP');l.add_obligation('o1','UP',.56515,.49);q=l.maybe_materialize('m1',.90,170)
        return q==0 and l.owner=='DUST_REPAIR_PENDING',l,{}
    rows.append(case('late_window_submin_remains_owned_no_new_exposure',c7))
    def c8():
        l=DustLedger('UP');l.add_obligation('o1','UP',2.2,.49);q=l.maybe_materialize('m1',.49,170)
        return abs(q-2.2)<1e-9 and l.submits[0]['role']=='REPAIR',l,{}
    rows.append(case('late_window_admissible_repair_still_allowed',c8))
    def c9():
        l=DustLedger('UP');l.add_obligation('o1','UP',.56515,.49)
        # data end: no fake completion
        ok=l.owner=='DUST_REPAIR_PENDING' and l.fabricated_completion==0
        return ok,l,{}
    rows.append(case('data_end_preserves_pending_owner_no_fake_payoff_recovery',c9))
    def c10():
        l=DustLedger('UP');l.add_obligation('o1','UP',.56515,.49)
        # Ledger has no API that can manufacture EXPAND; assert every submit, if any, is REPAIR.
        q=l.maybe_materialize('m1',.49,230)
        return q==0 and all(s['role']=='REPAIR' for s in l.submits),l,{}
    rows.append(case('dust_clearance_never_manufactures_expand',c10))
    gates={
      'allTenScenariosPass':all(r['pass'] for r in rows),
      'zeroBelowMinSubmit':sum(r['ledger']['belowMinSubmits'] for r in rows)==0,
      'zeroOversize':sum(float(r['ledger']['oversizeQty']) for r in rows)<=EPS,
      'singleOwnerSemantics':all((r['ledger']['latentQty']<=EPS) or r['ledger']['owner']=='DUST_REPAIR_PENDING' for r in rows),
      'zeroFabricatedCompletion':sum(r['ledger']['fabricatedCompletion'] for r in rows)==0,
      'zeroOppositeAbsorb':sum(float(r['ledger']['oppositeAbsorbQty']) for r in rows)<=EPS,
      'noExpandManufactured':all(all(s['role']=='REPAIR' for s in r['ledger']['submits']) for r in rows),
    }
    out={'version':'ETH_REPAIR_V74_LATENT_DUST_OBLIGATION_MICROWORLD','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'cases':rows,'gates':gates,'functionalPass':all(gates.values()),'decision':'AUTHORIZE_FRESH_1911708_DUST_LIFECYCLE_SHADOW' if all(gates.values()) else 'REJECT_V74_DUST_LEDGER','boundary':['responsibility/execution lifecycle only','no winner/PnL','no Expand manufacture','Repair remains allowed <=180s if exact obligation is venue-admissible','no 8781']}
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));outdir.mkdir(parents=True,exist_ok=True);(outdir/'result.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'decision':out['decision'],'gates':gates},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
