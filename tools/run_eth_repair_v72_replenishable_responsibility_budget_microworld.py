from __future__ import annotations
import json,os
from pathlib import Path
EPS=1e-9

class Ledger:
    def __init__(self,initial_budget=5.0,side='DOWN'):
        self.side=side;self.pay_side='UP' if side=='DOWN' else 'DOWN'
        self.available=float(initial_budget);self.initial=float(initial_budget)
        self.debt=0.0;self.expand_fill=0.0;self.repair_paid=0.0
        self.credits=[];self.seen=set();self.events=[];self.overspend=0.0;self.credit_reuse=0.0
    def expand_fill_event(self,event_id,qty,price,route='PASSIVE',seconds_left=240):
        if event_id in self.seen:return 0.0
        self.seen.add(event_id);q=max(0.0,float(qty));p=float(price)
        fill=min(q,max(0.0,self.available));self.overspend+=max(0.0,q-fill)
        self.available-=fill;self.debt+=fill;self.expand_fill+=fill
        self.events.append({'event':'EXPAND_FILL','id':event_id,'route':route,'qty':fill,'price':p,'debt':self.debt,'available':self.available,'secondsLeft':seconds_left})
        return fill
    def repair_fill_event(self,event_id,qty,price,side=None,t=0):
        if event_id in self.seen:return 0.0
        self.seen.add(event_id);side=side or self.pay_side;q=max(0.0,float(qty));p=float(price)
        if side!=self.pay_side:
            self.events.append({'event':'REPAIR_WRONG_SIDE','id':event_id,'qty':q,'price':p});return 0.0
        paid=min(q,max(0.0,self.debt));self.debt-=paid;self.repair_paid+=paid
        if paid>EPS:self.credits.append({'id':event_id,'qty':paid,'remaining':paid,'repairPrice':p,'t':t})
        self.events.append({'event':'REPAIR_PAYMENT','id':event_id,'qty':paid,'price':p,'debt':self.debt,'creditMinted':paid})
        return paid
    def replenish(self,event_id,expand_price,requested_qty,seconds_left=240):
        if event_id in self.seen:return 0.0
        self.seen.add(event_id);pe=float(expand_price);need=max(0.0,float(requested_qty))
        if seconds_left<=180:
            self.events.append({'event':'REPLENISH_BLOCK_CUTOFF','id':event_id,'qty':0.0,'expandPrice':pe});return 0.0
        got=0.0
        for c in self.credits:
            if got>=need-EPS:break
            rem=max(0.0,float(c['remaining']))
            if rem<=EPS:continue
            if float(c['repairPrice'])+pe>1.0+EPS:continue
            use=min(rem,need-got);c['remaining']-=use;got+=use
        self.available+=got
        self.events.append({'event':'REPLENISH','id':event_id,'qty':got,'expandPrice':pe,'available':self.available})
        return got
    def credit_remaining(self):return sum(max(0.0,float(c['remaining'])) for c in self.credits)

def run_case(name,fn):
    try:
        d=fn();ok=bool(d.pop('ok'));return {'name':name,'pass':ok,**d}
    except Exception as e:return {'name':name,'pass':False,'error':f'{type(e).__name__}:{e}'}

def c1():
    l=Ledger(5);f=l.expand_fill_event('e1',3,.4);return {'ok':abs(f-3)<EPS and abs(l.debt-3)<EPS and abs(l.available-2)<EPS,'ledger':vars(l)}
def c2():
    l=Ledger(5);a=l.expand_fill_event('e1',3,.4,'PASSIVE');b=l.expand_fill_event('e2',2,.42,'ACTIVE');return {'ok':abs(a+b-5)<EPS and abs(l.available)<EPS and abs(l.debt-5)<EPS,'ledger':vars(l)}
def c3():
    l=Ledger(2);f=l.expand_fill_event('e1',5,.4);return {'ok':abs(f-2)<EPS and abs(l.overspend-3)<EPS and l.available>=-EPS,'ledger':vars(l)}
def c4():
    l=Ledger(3);l.expand_fill_event('e1',3,.4);p=l.repair_fill_event('r1',2,.55);return {'ok':abs(p-2)<EPS and abs(l.debt-1)<EPS and abs(l.credit_remaining()-2)<EPS,'ledger':vars(l)}
def c5():
    l=Ledger(2);l.expand_fill_event('e1',2,.4);l.repair_fill_event('r1',2,.55);r=l.replenish('x1',.40,2);f=l.expand_fill_event('e2',2,.40,'ACTIVE');return {'ok':abs(r-2)<EPS and abs(f-2)<EPS and abs(l.debt-2)<EPS and abs(l.credit_remaining())<EPS,'ledger':vars(l)}
def c6():
    l=Ledger(2);l.expand_fill_event('e1',2,.4);l.repair_fill_event('r1',2,.65);r=l.replenish('x1',.40,2);return {'ok':abs(r)<EPS and abs(l.available)<EPS and abs(l.credit_remaining()-2)<EPS,'ledger':vars(l)}
def c7():
    l=Ledger(2);l.expand_fill_event('e1',2,.4);l.repair_fill_event('r1',2,.5);r1=l.replenish('x1',.45,2);r2=l.replenish('x2',.45,2);return {'ok':abs(r1-2)<EPS and abs(r2)<EPS and abs(l.credit_remaining())<EPS,'ledger':vars(l)}
def c8():
    l=Ledger(4);l.expand_fill_event('e1',4,.35);l.repair_fill_event('r1',1.5,.55);r=l.replenish('x1',.40,4);return {'ok':abs(r-1.5)<EPS and abs(l.available-1.5)<EPS and abs(l.debt-2.5)<EPS,'ledger':vars(l)}
def c9():
    l=Ledger(3);a=l.expand_fill_event('e1',2,.4);b=l.expand_fill_event('e1',2,.4);l.repair_fill_event('r1',1,.5);c=l.repair_fill_event('r1',1,.5);return {'ok':abs(a-2)<EPS and abs(b)<EPS and abs(c)<EPS and abs(l.expand_fill-2)<EPS and abs(l.repair_paid-1)<EPS,'ledger':vars(l)}
def c10():
    l=Ledger(2);l.expand_fill_event('e1',2,.4,seconds_left=190);l.repair_fill_event('r1',2,.5);r=l.replenish('x1',.45,2,seconds_left=180);return {'ok':abs(r)<EPS and abs(l.debt)<EPS and abs(l.repair_paid-2)<EPS and abs(l.credit_remaining()-2)<EPS,'ledger':vars(l)}

def main():
    cases=[run_case('initial_budget_fill_births_debt',c1),run_case('passive_active_share_budget',c2),run_case('overspend_clipped',c3),run_case('repair_pays_and_mints_credit',c4),run_case('favorable_pair_replenishes',c5),run_case('unfavorable_pair_blocked',c6),run_case('credit_one_use',c7),run_case('partial_repair_partial_replenish',c8),run_case('duplicate_fill_idempotent',c9),run_case('cutoff_blocks_replenish_not_repair',c10)]
    gates={
      'allTenScenariosPass':all(x['pass'] for x in cases),
      'physicalExpandFillEqualsDebtBirth':all(x['pass'] for x in [cases[0],cases[1],cases[4]]),
      'repairDebtPaymentConserved':all(x['pass'] for x in [cases[3],cases[7],cases[9]]),
      'noBudgetOverspend':cases[2]['pass'],
      'noRepairCreditReuse':cases[6]['pass'],
      'unfavorablePairCannotReplenish':cases[5]['pass'],
      'favorablePairCanReplenish':cases[4]['pass'],
      'passiveActiveShareOneBudget':cases[1]['pass'],
      'duplicateFillIdempotent':cases[8]['pass'],
      'cutoffBlocksReplenishmentButNotRepair':cases[9]['pass']}
    out={'version':'ETH_REPAIR_V72_REPLENISHABLE_RESPONSIBILITY_BUDGET_MICROWORLD','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'cases':cases,'gates':gates,'functionalPass':all(gates.values()),'decision':'AUTHORIZE_ONE_MARKET_V72_HFT_INSTRUMENTED_SMOKE' if all(gates.values()) else 'FIX_LEDGER_BEFORE_HFT','boundary':['structural pair-sum <=1 only','no learned threshold','no PnL/winner','no dream fill','no 8781']}
    outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));outdir.mkdir(parents=True,exist_ok=True);(outdir/'result.json').write_text(json.dumps(out,indent=2,default=lambda o: sorted(o) if isinstance(o,set) else str(o)),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'decision':out['decision'],'gates':gates},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
