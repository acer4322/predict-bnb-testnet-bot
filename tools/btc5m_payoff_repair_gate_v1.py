"""Offline same-prefix quantity versus cash service admission. No Target event inputs."""
import math
MODES=('INERT','ISOLATED_QUANTITY','ISOLATED_PAYOFF_ZERO','ISOLATED_PAYOFF_MINUS_ONE')


def snapshot(frame,ledger):
    inv={s:float(frame['own_view']['inv'][s]) for s in ('UP','DOWN')}
    cost=float(frame['own_view']['cost']); qty={'UP':0.,'DOWN':0.}; cash=dict(qty); owners=[]
    for k,c in ledger.carriers.items():
        if c.state=='TERMINAL':continue
        assert c.route=='PASSIVE' and c.fee_cap==0., 'panel requires the frozen zero-fee passive reservation contract'
        side=ledger.grants[c.parent_id].side; rem=max(0.,float(c.qty)-float(c.filled))
        qty[side]+=rem;cash[side]+=rem*float(c.limit)
        owners.append(dict(key=k,side=side,state=c.state,qty=rem,limit=float(c.limit)))
    return dict(inv=inv,cost=cost,payoff={s:inv[s]-cost for s in inv},pending_qty=qty,pending_cash=cash,owners=owners)


def capacity(state,side,price,requested,mode):
    if mode=='INERT':return requested,dict(reason='INERT')
    if side=='UP':return 0.,dict(reason='FIX_NEW_UP_ADMISSION_OFF')
    inv=state['inv'];pending=state['pending_qty'];cash=state['pending_cash']
    quantity_need=max(0.,inv['UP']-inv['DOWN']-pending['DOWN'])
    if mode=='ISOLATED_QUANTITY':
        return min(requested,quantity_need),dict(reason='QUANTITY_CAPACITY',quantity_need=quantity_need)
    bound=0. if mode=='ISOLATED_PAYOFF_ZERO' else -1.
    # Full-fill reservation scenario, not a claim that pending has already paid.
    # All DOWN pending supplies potential payoff; all UP pending can consume cash.
    projected_down=state['payoff']['DOWN']+pending['DOWN']-cash['DOWN']-cash['UP']
    money_need=max(0.,(bound-projected_down)/(1.-price))
    return min(requested,quantity_need,money_need),dict(reason='PAYOFF_CAPACITY',quantity_need=quantity_need,
        money_need=money_need,payoff_bound=bound,pending_full_fill_down_payoff=projected_down)


class PayoffRepairGate:
    def __init__(self,selection,mode):
        assert mode in MODES
        self.selection=selection;self.mode=mode;self.started=False;self.events=[];self.rows=[]

    def on_frame(self,f,producer):
        if f['t']<self.selection['t']:return
        if not self.started:
            assert f['t']==self.selection['t']
            lot=next(r for q in producer.atomic.q.values() for r in q if r['id']==self.selection['old_id'])
            assert abs(lot['remaining']-self.selection['initial_remaining'])<1e-7
            self.started=True
            self.events.append(dict(event='START',t=f['t'],quantity_step=f['world_profile']['quantity_step'],
                old_remaining=lot['remaining'],state=snapshot(f,f['ledger'])))

    def cap(self,f,side,price,qty,draft):
        if not self.started:return qty
        state=snapshot(f,draft)
        limit,why=capacity(state,side,price,qty,self.mode)
        step=float(f['world_profile']['quantity_step'])
        admitted=qty if self.mode=='INERT' else min(qty,round(math.floor((limit+1e-10)/step)*step,8))
        self.rows.append(dict(t=f['t'],side=side,price=price,requested=qty,capped=admitted,state=state,**why))
        return admitted


def instrument(source,replace):
    source=replace(source,'self.intent=_ExposureIntent(_MODE)',
        'self.intent=_ExposureIntent(_MODE);self.money_gate=_MoneyGate(_MONEY_SELECTION,_MONEY_MODE)')
    marker="    if f['t']>=f['end']:\n"
    source=replace(source,marker,"    self.money_gate.on_frame(f,self)\n"+marker)
    lines=[];count=0
    for line in source.splitlines():
        if "try:validate_size(f['world_profile']['asset'],'PASSIVE',price,qty,quantity_step=step)" in line:
            var='ss' if count<2 else 's';indent=line[:len(line)-len(line.lstrip())]
            lines.append(indent+f'qty=self.money_gate.cap(f,{var},price,qty,draft)')
            lines.append(indent+'if qty<=0:continue')
            count+=1
        lines.append(line)
    assert count==4,count
    return '\n'.join(lines)+'\n'


def self_test():
    # Same shares and prices, different accrued cash -> different service demand.
    base=dict(inv={'UP':100.,'DOWN':60.},payoff={'UP':35.,'DOWN':-5.},
              pending_qty={'UP':0.,'DOWN':0.},pending_cash={'UP':0.,'DOWN':0.})
    q,_=capacity(base,'DOWN',.5,40.,'ISOLATED_QUANTITY');assert q==40.
    q,_=capacity(base,'DOWN',.5,40.,'ISOLATED_PAYOFF_ZERO');assert q==10.
    q,_=capacity(base,'DOWN',.5,40.,'ISOLATED_PAYOFF_MINUS_ONE');assert q==8.
    safe=dict(base,payoff={'UP':45.,'DOWN':5.})
    assert capacity(safe,'DOWN',.5,40.,'ISOLATED_PAYOFF_ZERO')[0]==0.
    pending=dict(base,pending_qty={'UP':0.,'DOWN':10.},pending_cash={'UP':0.,'DOWN':5.})
    assert capacity(pending,'DOWN',.5,40.,'ISOLATED_PAYOFF_ZERO')[0]==0.
    # A terminal release without fill restores demand; cancel intent is still pending.
    assert capacity(base,'DOWN',.5,40.,'ISOLATED_PAYOFF_ZERO')[0]==10.
    adverse=dict(pending,pending_cash={'UP':5.,'DOWN':5.})
    assert capacity(adverse,'DOWN',.5,40.,'ISOLATED_PAYOFF_ZERO')[0]==10.
    assert capacity(base,'UP',.5,30.,'ISOLATED_QUANTITY')[0]==0.


if __name__=='__main__':self_test();print('PASS')
